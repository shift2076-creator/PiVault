from django.db import migrations

RETIRED = ('inventory', 'pantry')


def _without(values):
    return {key: value for key, value in (values or {}).items() if key not in RETIRED}


def remove_retired(apps, schema_editor):
    """Inventory and Pantry were removed: drop their stored access, vault entries and invitation grants."""
    User = apps.get_model('portal', 'User')
    Invitation = apps.get_model('portal', 'Invitation')
    for user in User.objects.all():
        access, items = _without(user.service_access), _without(user.vault_items)
        if access != user.service_access or items != user.vault_items:
            user.service_access, user.vault_items = access, items
            user.save(update_fields=['service_access', 'vault_items'])
    for invitation in Invitation.objects.all():
        services = _without(invitation.services)
        if services != invitation.services:
            invitation.services = services
            invitation.save(update_fields=['services'])


class Migration(migrations.Migration):
    dependencies = [('portal', '0005_devices')]
    operations = [migrations.RunPython(remove_retired, migrations.RunPython.noop)]
