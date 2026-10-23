#!/usr/bin/env python3
"""Prepare an offline rights dossier for one exact transfer; never invent a grant."""
import argparse
import datetime
import hashlib
import json
import pathlib
import shutil


def prepare(evidence, key, provider, url, size, md5, destination, loc_metadata=None):
    evidence, destination = pathlib.Path(evidence), pathlib.Path(destination)
    record = next(r for r in json.loads((evidence/'catalogue.json').read_text()) if r['key']==key)
    destination.mkdir(parents=True, exist_ok=True)
    license_url = record['license'] if provider=='archive' and record['license']!='-' else None
    names = ['LoC-public-domain-guidance.html', 'snapshot-manifest.json']
    if provider=='archive':
        shutil.copyfile(evidence/'archive-metadata'/(key+'.json'), destination/'source-metadata.json')
        if license_url and license_url!='-':
            names.append('CC-BY-3.0.txt' if '/by/3.0/' in license_url else
                         'CC0-1.0.txt' if '/zero/' in license_url else
                         'Public-Domain-Mark.html' if '/mark/' in license_url else
                         'Public-Domain-Certification.html')
        page=record['item']
    elif provider=='loc':
        # Preserve the exact LoC response used to select this transfer.
        metadata=pathlib.Path(loc_metadata)
        json.loads(metadata.read_text())
        shutil.copyfile(metadata, destination/'source-metadata.json')
        page=record['loc']
    else:
        raise ValueError('Unknown source provider')
    for name in names:
        shutil.copyfile(evidence/name, destination/name)
    selected={'film':record['title'],'year':record['year'],'key':key,'provider':provider,
              'source_page':page,'download_url':url,'bytes':int(size),'published_md5':md5,
              'saved_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'declared_license_url':license_url,'catalogue_rights_basis':record['rights'],
              'source_metadata_sha256':hashlib.sha256((destination/'source-metadata.json').read_bytes()).hexdigest()}
    (destination/'transfer.json').write_text(json.dumps(selected,indent=2)+'\n')
    creator=record['creator']
    if isinstance(creator,list): creator='; '.join(map(str,creator))
    attribution = (f"Creator(s) as supplied by source: {creator or 'Not supplied; see original source metadata.'}\n"
                   if provider=='archive' else 'Creator/credit statements: see preserved LoC source-metadata.json.\n')
    text=(f"{record['title']} ({record['year']}) — rights and provenance\n\n"
          f"Source page: {page}\nExact transfer: {url}\nBytes: {size}\nPublished MD5: {md5}\n"
          f"Catalogue rights basis: {record['rights']}\n"
          f"Source license/declaration: {license_url or ('No machine-readable license supplied; see source metadata and catalogue rights basis.' if provider=='archive' else 'See LoC rights/advisory fields in source-metadata.json.')}\n"
          +attribution+
          "PiVault changes: none; original downloaded bytes retained.\n\n"
          "Source declarations are preserved as evidence, not a new license granted by PiVault.\n"
          "A public-domain film may have no license document. Later scores and editions can have separate rights.\n"
          "The archive declaration applies only to the archive transfer; it is not assigned to a LoC copy.\n"
          "Retain this complete folder when copying or sharing the movie, including source credits and notices.\n")
    (destination/'RIGHTS.txt').write_text(text)


def main():
    parser=argparse.ArgumentParser()
    for name in ('evidence','key','provider','url','bytes','md5','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--loc-metadata')
    args=parser.parse_args()
    prepare(args.evidence,args.key,args.provider,args.url,args.bytes,args.md5,args.output,args.loc_metadata)

if __name__=='__main__': main()
