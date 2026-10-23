import json,uuid
from pathlib import Path
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render,redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.debug import sensitive_post_parameters
from django.db import transaction
from .models import User
from .services import ADAPTERS
from .integrations.vaultwarden import VaultHTTP,enc_string
from .integrations.native_http import NativeError

@require_http_methods(['GET','POST'])
@sensitive_post_parameters()
def repair(request):
    if not request.user.is_authenticated:
        return redirect('/users/sign-in') if request.method=='GET' else JsonResponse({'error':'Sign in to PiVault again.'},status=401)
    if request.user.role!='admin' or not request.user.vault_user_id:
        return HttpResponseForbidden('An existing Admin vault is required.')
    rows=json.loads((Path(__file__).parent/'vault-repair-services.json').read_text())
    if request.method=='GET':
        return render(request,'vault-repair.html',{'rows':rows,'email':request.user.email})
    try:
        data=json.loads(request.body)
        adapter=ADAPTERS['passwords']
        client=VaultHTTP('Password Manager',adapter.client.base_url)
        op=data.get('op')
        if op=='login':
            digest=data['hash']
            if not isinstance(digest,str) or len(digest)>128:raise ValueError()
            _,result=client.form('/identity/connect/token',{'grant_type':'password','client_id':'web','scope':'api offline_access','username':request.user.email,'password':digest,'deviceType':'9','deviceIdentifier':str(uuid.uuid4()),'deviceName':'PiVault credential repair'})
            token=result['access_token']
        else:token=data['token']
        if not isinstance(token,str) or not token or len(token)>20000 or any(c in token for c in '\r\n'):raise ValueError()
        client=VaultHTTP('Password Manager',adapter.client.base_url,{'Authorization':'Bearer '+token})
        profile=client.request('GET','/api/accounts/profile')
        if profile.get('id')!=request.user.vault_user_id or profile.get('email','').casefold()!=request.user.email.casefold():
            return JsonResponse({'error':'The authenticated vault does not match your PiVault account.'},status=403)
        if op=='login':
            sync=client.request('GET','/api/sync')
            response=JsonResponse({'token':token,'key':profile.get('key') or result.get('Key') or result.get('key'),'ciphers':sync.get('ciphers',[]),'saved':request.user.vault_items})
        elif op=='add':
            service=data['service']
            if service not in {r['id'] for r in rows}:raise ValueError()
            item=data['item']
            if set(item)!={'name','username','password','uri'}:raise ValueError()
            for value in item.values():enc_string(value,4096)
            with transaction.atomic():
                user=User.objects.select_for_update().get(pk=request.user.pk)
                # Serialize retries and concurrent tabs against the current vault contents.
                sync=client.request('GET','/api/sync')
                ciphers=sync.get('ciphers',[])
                actual=sorted(c['id'] for c in ciphers)
                if actual!=sorted(data['expected_ids']):
                    return JsonResponse({'error':'Vault changed. Unlock again before saving more entries.'},status=409)
                if service in (user.vault_items or {}) and user.vault_items[service] in actual:
                    return JsonResponse({'skipped':True,'ids':actual})
                cipher=client.request('POST','/api/ciphers',{'type':1,'name':item['name'],'encryptedFor':user.vault_user_id,'folderId':None,'organizationId':None,'favorite':False,'reprompt':0,'login':{'username':item['username'],'password':item['password'],'uris':[{'uri':item['uri'],'match':1}]}})
                if not isinstance(cipher,dict) or not cipher.get('id'):raise ValueError()
                user.vault_items={**(user.vault_items or {}),service:cipher['id']}
                user.save(update_fields=['vault_items'])
                response=JsonResponse({'id':cipher['id'],'ids':sorted(actual+[cipher['id']])})
        else:raise ValueError()
        response['Cache-Control']='no-store, private'
        return response
    except NativeError as error:
        return JsonResponse({'error':'Vault request failed'+(' (HTTP '+str(error.status)+')' if error.status else '')+'. No password was reset. Unlock again to refresh the vault before retrying.'},status=502)
    except (KeyError,ValueError,TypeError):
        return JsonResponse({'error':'The vault response or submitted data was invalid. Unlock again before retrying.'},status=400)
