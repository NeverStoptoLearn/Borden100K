"""Optional offline official OGC + Google gx XSD validation (requires lxml).

Original schemas remain byte-exact. A no-network resolver maps official imports
to the explicitly supplied directory; this tool does not fetch any resource.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


def verify(kml_path, schema_directory):
    from lxml import etree
    directory=Path(schema_directory).resolve()
    names=('ogckml22.xsd','kml22gx.xsd','atom-author-link.xsd','xAL.xsd')
    class Local(etree.Resolver):
        def resolve(self,url,public_id,context):
            name=Path(urlparse(url).path).name
            if name not in names:raise ValueError('Unapproved schema import: '+name)
            return self.resolve_filename(str(directory/name),context)
    parser=etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False)
    parser.resolvers.add(Local())
    schema=etree.XMLSchema(etree.parse(str(directory/'kml22gx.xsd'),parser))
    raw=Path(kml_path).read_bytes()
    xml=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))
    schema.assertValid(xml)
    return dict(structural_validation='PASS',client_import='NOT_RUN',kml_sha256=hashlib.sha256(raw).hexdigest(),schema_sha256={n:hashlib.sha256((directory/n).read_bytes()).hexdigest() for n in names})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('kml',type=Path);p.add_argument('--schemas',type=Path,required=True);p.add_argument('--report',type=Path)
    a=p.parse_args();result=verify(a.kml,a.schemas)
    if a.report:
        a.report.parent.mkdir(parents=True,exist_ok=True)
        with a.report.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
