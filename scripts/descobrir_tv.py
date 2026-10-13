"""Descobre descricoes UPnP/DLNA e informa AVTransport, sem controlar a TV."""
import argparse
import ipaddress
import json
import socket
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NS='{urn:schemas-upnp-org:device-1-0}'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def private_url(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='http' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use um URL HTTP de descricao na sua rede local.')
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 80,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_private for item in addresses):
        raise ValueError('Somente enderecos da rede local sao aceitos.')
    return url

def parse_description(xml,url):
    root=ET.fromstring(xml)
    base=root.findtext(NS+'URLBase') or url
    result=[]
    for device in root.findall('.//'+NS+'device'):
        for service in device.findall(NS+'serviceList/'+NS+'service'):
            service_type=service.findtext(NS+'serviceType') or ''
            if ':service:AVTransport:' in service_type:
                control=urllib.parse.urljoin(base,service.findtext(NS+'controlURL') or '')
                parsed=urllib.parse.urlparse(control)
                result.append({'name':device.findtext(NS+'friendlyName'),
                    'manufacturer':device.findtext(NS+'manufacturer'),'model':device.findtext(NS+'modelName'),
                    'service_type':service_type,'description_url':url,'control_url':control,
                    'control_port':parsed.port or 80,'control_path':parsed.path+('?' + parsed.query if parsed.query else '')})
    return result

def discover(timeout):
    message=('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
             'MX: 2\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n').encode()
    urls=set()
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.settimeout(.5)
        udp.sendto(message,('239.255.255.250',1900))
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try: data,_=udp.recvfrom(65535)
            except socket.timeout: continue
            for line in data.decode('utf-8',errors='replace').splitlines():
                if line.lower().startswith('location:'):
                    urls.add(line.split(':',1)[1].strip())
    return sorted(urls)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--description-url')
    parser.add_argument('--timeout',type=float,default=4)
    args=parser.parse_args()
    urls=[args.description_url] if args.description_url else discover(max(1,min(args.timeout,15)))
    found=[]
    for url in urls:
        try:
            private_url(url)
            with OPENER.open(url,timeout=5) as response: xml=response.read(2000000)
            found.extend(parse_description(xml,url))
        except Exception as exc: print('Nao foi possivel ler descricao:',str(exc))
    print(json.dumps(found,ensure_ascii=False,indent=2))
    if not found: print('Nenhum AVTransport encontrado. Confira rede, TV e multicast.')

if __name__=='__main__': main()
