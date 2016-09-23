#!/usr/bin/env python3
import json
from base64 import b64decode
from ipaddress import ip_address, IPv4Address
from subprocess import Popen, PIPE
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler


BIND_ADDRESS = ('127.0.0.1', 8080)
DOMAIN_SUFFIX = ".dyn.example.com"
TTL = 180
TIMEOUT = 3
MAX_ADDR_PRE_NAME = 32
PASSWORD_FILE = "/var/named/ddns-hosts.json"
NSUPDATE = "/usr/bin/nsupdate"


class HTTPRequestHandler(BaseHTTPRequestHandler):
    _host_ip_cache = {}

    def send(self, message, status=200):
        self.send_response(status)
        self.end_headers()
        self.wfile.write(message.encode())

    def send_unauthorized(self):
        self.send_response(401, 'Not Authorized')
        self.send_header('WWW-Authenticate', 
                         'Basic realm="%s"' % DOMAIN_SUFFIX)
        self.end_headers()
        self.wfile.write(b'no auth')

    def do_GET(self):
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Basic '):
            self.send_unauthorized()
            return

        host, pwd = b64decode(auth[6:]).decode().split(':', 1)
        if host.endswith(DOMAIN_SUFFIX):
            host = host[:-len(DOMAIN_SUFFIX)]
        if self.server.host_auth.get(host) != pwd:
            self.send_unauthorized()
            return

        args = parse_qs(urlparse(self.path).query)
        if 'ip' in args:
            ip = [s.strip() for s in args['ip']]
        elif 'X-Real-IP' in self.headers:
            ip = [self.headers['X-Real-IP']]
        else:
            self.send('no address', 400)
            return

        try:
            ip = {ip_address(a) for a in ip}
        except AddressValueError as e:
            self.send('broken address\n%s' % e, 400)
            return

        if len(ip) > MAX_ADDR_PRE_NAME:
            self.send('too many addresses\nmax %s' % MAX_ADDR_PRE_NAME, 400)
            return

        if self._host_ip_cache.get(host) == ip:
            self.send('no-change', 200)
            return

        ok, msg = update_record(host + DOMAIN_SUFFIX, ip)
        if ok:
            self._host_ip_cache[host] = ip
            self.send(msg, 200)
        else:
            self.send(msg, 500)


def update_record(domain, addrs):
    nsupdate = Popen([NSUPDATE, '-l'], universal_newlines=True,
                     stdin=PIPE, stdout=PIPE, stderr=PIPE)
    cmdline = ["del %s" % domain]
    for addr in addrs:
        type = 'A' if isinstance(addr, IPv4Address) else 'AAAA'
        cmdline += ["add {domain} {ttl} {type} {ip}"
                    .format(ttl=TTL, domain=domain, ip=addr, type=type)]
    cmdline += ["send", "quit"]
    try:
        outs, errs = nsupdate.communicate('\n'.join(cmdline), 3)
    except TimeoutExpired:
        nsupdate.kill()
        return False, "timeout"

    if errs:
        return False, errs
    else:
        return True, "success"


def main():
    server = HTTPServer(BIND_ADDRESS, HTTPRequestHandler)
    server.host_auth = json.load(open(PASSWORD_FILE))
    server.serve_forever()


if __name__ == '__main__':
    main()
