"""Default invocation is synthetic and never uses network or secrets."""
import argparse
import json
from pathlib import Path
import sys
from .state import State
from .fetch import refresh,http_get,Response
from .pipeline import aggregate
from .export import publish

DEMO=b'<tv><channel id="demo"><display-name>Demo</display-name></channel><programme channel="demo" start="20261117090000 +0000" stop="20261117100000 +0000"><title>Synthetic demonstration</title></programme></tv>'


def run(state_path,output,config=None):
    config=json.loads(Path(config).read_text()) if config else {}
    sources=config.get('sources',[])
    ids=[s.get('id') for s in sources]
    if len(set(ids))!=len(ids): raise ValueError('duplicate configured sources')
    # Explicit local input only until rights and protected persistence are reviewed.
    # No remote source is activated by merely supplying an address.
    if any('url' in s for s in sources):
        raise ValueError('network source activation awaits separate release review')
    aliases={}
    for mapping in config.get('aliases',[]):
        if mapping.get('confirmed') is not True:
            raise ValueError('unconfirmed alias')
        key=mapping['source']+':'+mapping['xmltv_id']
        if key in aliases: raise ValueError('conflicting alias')
        aliases[key]=mapping['canonical_id']
    trees=[]
    with State(state_path) as state:
        for source in sources:
            def fetch(headers,source=source):
                try:
                    with Path(source['path']).open('rb') as f:
                        return Response(200,f.read(32*1024*1024+1),{})
                except OSError:
                    raise ValueError('local source unavailable') from None
            try:
                tree,status=refresh(state,source['id'],fetch,aliases)
            except ValueError:
                continue  # independent source failure; never discard other valid sources
            trees.append((source.get('priority',100),tree))
        if not sources:
            tree,status=refresh(state,'synthetic',lambda h:Response(200,DEMO,{}))
            trees=[(1,tree)]
        minimum=config.get('minimum_sources',1)
        if type(minimum) is not int or minimum<1 or len(trees)<minimum:
            raise ValueError('source availability below configured threshold')
        tree,report=aggregate(trees,state,logos=config.get('logos'))
        return publish(tree,Path(output))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--config',type=Path)
    args=parser.parse_args()
    try:
        result=run(args.state,args.output,args.config)
        print(json.dumps({'status':result['status'],'version':result['version']}))
    except (ValueError,OSError,KeyError,TypeError):
        print('EPG processing failed; previous manifest retained',file=sys.stderr)
        return 1
    return 0
