"""Conservative TMDB movie matching; optional live client only for trusted Actions."""
import json
import re
import time
import unicodedata
from urllib.parse import urlencode
from xml.etree import ElementTree as ET
from .fetch import http_get


def title_key(value):
    return ' '.join(unicodedata.normalize('NFKC',value if isinstance(value,str) else '').casefold().split())


def choose(title,year,hits):
    matches={}
    for hit in hits:
        if not isinstance(hit,dict): continue
        names=[hit.get('title',''),hit.get('original_title','')]
        if title_key(title) not in [title_key(n) for n in names]: continue
        if str(year)!=str(hit.get('release_date') or '')[:4]: continue
        if type(hit.get('id')) is int and hit['id']>0:
            matches[hit['id']]=hit
    return next(iter(matches.values())) if len(matches)==1 else None


def image_url(path):
    if path is None: return None
    if not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_-]+\.(jpg|png|webp)',path):
        raise ValueError('invalid CDN image path')
    return 'https://image.tmdb.org/t/p/w500'+path


class TMDB:
    def __init__(self,token=None,transport=http_get,sleep=time.sleep,*,managed_http=False):
        if managed_http and token is not None:
            raise ValueError('conflicting authentication modes')
        if not managed_http and (not isinstance(token,str) or not token or '\n' in token or '\r' in token):
            raise ValueError('missing or invalid bearer credential')
        self._token=token
        self._managed_http=bool(managed_http)
        self.transport=transport
        self.sleep=sleep

    @property
    def auth_mode(self):
        return 'managed_http' if self._managed_http else 'bearer'

    def clone(self,*,transport=None,sleep=None):
        """Keep the caller's explicit auth mode when applying bounded transport."""
        return TMDB(self._token, self.transport if transport is None else transport,
                    self.sleep if sleep is None else sleep, managed_http=self._managed_http)

    def _get(self,path,params=None):
        url='https://api.themoviedb.org/3/'+path+'?'+urlencode(params or {'language':'de-DE'})
        for attempt in range(3):
            try:
                headers={} if self._managed_http else {'Authorization':'Bearer '+self._token}
                response=self.transport(url,headers)
                if response.status==429 or 500<=response.status<=599:
                    raise TimeoutError()
                if response.status!=200: raise ValueError()
                data=json.loads(response.body)
                if not isinstance(data,dict): raise ValueError()
                return data
            except TimeoutError:
                if attempt<2:
                    self.sleep(2**attempt)
                    continue
            except (ValueError,TypeError,AttributeError):
                pass
            break
        raise ValueError('metadata lookup failed') from None

    def search(self,title,year):
        return self._search('movie',title,year)

    def search_series(self,title,year):
        return self._search('tv',title,year)

    def _search(self,kind,title,year):
        params={'query':title,'language':'de-DE','include_adult':'false'}
        params['year' if kind=='movie' else 'first_air_date_year']=year
        results=self._get('search/'+kind,params).get('results')
        if not isinstance(results,list): raise ValueError('metadata lookup failed')
        return results

    def episode(self,series,season,number):
        if any(not isinstance(n,int) or n<0 for n in (series,season,number)):
            raise ValueError('invalid episode identifiers')
        return self._get(f'tv/{series}/season/{season}/episode/{number}')

    def movie(self,identifier):
        if not isinstance(identifier,int) or identifier<=0: raise ValueError('invalid movie ID')
        return self._get(f'movie/{identifier}')

    def series(self,identifier):
        if type(identifier) is not int or identifier<=0: raise ValueError('invalid series ID')
        return self._get(f'tv/{identifier}')

    def season(self,identifier,season):
        if type(identifier) is not int or identifier<=0 or type(season) is not int or season<0:
            raise ValueError('invalid season identifiers')
        return self._get(f'tv/{identifier}/season/{season}')

    def movie_credits(self,identifier):
        if type(identifier) is not int or identifier<=0: raise ValueError('invalid movie ID')
        return self._get(f'movie/{identifier}/credits')

    def series_credits(self,identifier):
        if type(identifier) is not int or identifier<=0: raise ValueError('invalid series ID')
        return self._get(f'tv/{identifier}/aggregate_credits')

    def configuration(self):
        """Explicit bounded caller-requested metadata authentication check."""
        return self._get('configuration')


def enrich(root,state,search,details=None):
    for p in root.findall('programme'):
        # Unknown media type or episode metadata must never be guessed as a movie.
        if p.find('episode-num') is not None: continue
        categories={title_key(c.text or '') for c in p.findall('category')}
        if not categories.intersection({'movie','film','spielfilm'}): continue
        title=p.findtext('title','')
        year=p.findtext('date','')[:4]
        if not re.fullmatch(r'\d{4}',year): continue
        key=json.dumps(['movie',title_key(title),year,'de-DE'])
        found,hit=state.cached(key)
        if not found:
            try:
                hit=choose(title,year,search(title,int(year)))
                if hit:
                    if details:
                        detail=details(hit['id'])
                        if not isinstance(detail,dict) or detail.get('id')!=hit['id']:
                            continue
                        hit=dict(hit,**detail)
                    for field in ('poster_path','backdrop_path'):
                        image_url(hit.get(field))
                state.cache(key,hit,30*86400 if hit else 86400)
            except (ValueError,TimeoutError):
                continue  # transient errors never become negative cached matches
        if not hit: continue
        ET.SubElement(p,'episode-num',{'system':'tmdb'}).text='movie/'+str(hit['id'])
        if not p.findtext('desc') and isinstance(hit.get('overview'),str):
            ET.SubElement(p,'desc',{'lang':'de'}).text=hit['overview']
        original=hit.get('original_title')
        language=hit.get('original_language')
        if isinstance(original,str) and original!=title and isinstance(language,str):
            ET.SubElement(p,'title',{'lang':language}).text=original
        for genre in hit.get('genres',[]) if isinstance(hit.get('genres',[]),list) else []:
            if isinstance(genre,dict) and isinstance(genre.get('name'),str):
                ET.SubElement(p,'category',{'lang':'de'}).text=genre['name']
        rating=hit.get('vote_average')
        if type(rating) in (int,float) and 0<rating<=10:
            node=ET.SubElement(p,'star-rating',{'system':'TMDB'})
            ET.SubElement(node,'value').text=str(rating)+'/10'
        for field in ('poster_path','backdrop_path'):
            url=image_url(hit.get(field))
            if url: ET.SubElement(p,'icon',{'src':url})


def choose_series(title,year,hits):
    adapted=[dict(h,title=h.get('name',''),original_title=h.get('original_name',''),
                  release_date=h.get('first_air_date','')) for h in hits if isinstance(h,dict)]
    return choose(title,year,adapted)


def enrich_episode(root,state,search,episode):
    for p in root.findall('programme'):
        if p.find('episode-num[@system="tmdb"]') is not None: continue
        number=p.findtext('episode-num[@system="xmltv_ns"]','')
        match=re.fullmatch(r'(\d+)\.(\d+)\.',number)
        year=p.findtext('date','')[:4]
        if not match or not re.fullmatch(r'\d{4}',year): continue
        season,ep=(int(v)+1 for v in match.groups())
        title=p.findtext('title','')
        key=json.dumps(['tv',title_key(title),year,season,ep,'de-DE'])
        found,result=state.cached(key)
        if not found:
            try:
                hit=choose_series(title,int(year),search(title,int(year)))
                result=None
                if hit:
                    detail=episode(hit['id'],season,ep)
                    if detail.get('season_number')!=season or detail.get('episode_number')!=ep:
                        continue
                    still=image_url(detail.get('still_path'))
                    result={'id':hit['id'],'still':still}
                state.cache(key,result,30*86400 if result else 86400)
            except (ValueError,TimeoutError): continue
        if result:
            ET.SubElement(p,'episode-num',{'system':'tmdb'}).text=f'tv/{result["id"]}/{season}/{ep}'
            if result['still']: ET.SubElement(p,'icon',{'src':result['still']})


class TVMaze:
    """Optional fallback transport. Redistribution requires TVMaze attribution review."""
    def __init__(self,transport=http_get):
        self.transport=transport

    def search_series(self,title,year):
        try:
            response=self.transport('https://api.tvmaze.com/search/shows?'+urlencode({'q':title}),{})
            if response.status!=200: raise ValueError()
            rows=json.loads(response.body)
            if not isinstance(rows,list): raise ValueError()
            return [dict(row['show'],first_air_date=row['show'].get('premiered') or '') for row in rows]
        except (ValueError,KeyError,TypeError,TimeoutError):
            raise ValueError('metadata lookup failed') from None
