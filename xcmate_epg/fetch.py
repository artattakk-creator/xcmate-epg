"""Conditional fetch; validation precedes every state mutation."""
from dataclasses import dataclass
import hashlib
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from .xmltv import parse, LIMIT


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def http_get(url,headers=None):
    parts=urlsplit(url)
    if parts.scheme != 'https' or parts.username or parts.password:
        raise ValueError('HTTPS URL without credentials required')
    request=Request(url,headers=headers or {})
    try:
        with build_opener(NoRedirect).open(request,timeout=20) as response:
            body=response.read(LIMIT+1)
            if len(body)>LIMIT:
                raise ValueError('download exceeds size limit')
            return Response(response.status,body,dict(response.headers))
    except HTTPError as error:
        # Never expose exception text: it can contain private request URLs.
        return Response(error.code,b'',dict(error.headers))
    except (URLError,OSError):
        raise TimeoutError('request failed') from None


def refresh(state,key,fetch,aliases=None,sleep=time.sleep):
    old=state.source(key)
    headers={}
    if old:
        if old['etag']: headers['If-None-Match']=old['etag']
        if old['modified']: headers['If-Modified-Since']=old['modified']
    for attempt in range(3):
        try:
            response=fetch(headers)
            if response.status==304:
                if not old: raise ValueError('304 without cached source')
                return parse(old['body'],key,aliases),'unchanged'
            if response.status==429 or 500<=response.status<=599:
                raise TimeoutError('temporary source failure')
            if response.status!=200: raise ValueError('source unavailable')
            root=parse(response.body,key,aliases)
            sha=hashlib.sha256(response.body).hexdigest()
            changed=not old or old['sha']!=sha
            h={k.lower():v for k,v in response.headers.items()}
            state.save_source(key,response.body,sha,h.get('etag'),h.get('last-modified'))
            return root,'changed' if changed else 'unchanged'
        except (TimeoutError,ConnectionError):
            if attempt<2:
                sleep(2**attempt)
                continue
        except ValueError:
            pass
        break
    if old:
        return parse(old['body'],key,aliases),'lastgood'
    raise ValueError('source failed without valid snapshot') from None
