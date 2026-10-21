#!/usr/bin/env python3
"""Local vTuner replacement for old Pioneer (and other) AV receivers.

The receiver asks http://pioneer.vtuner.com/setupapp/... for its Internet
Radio menu. Point that hostname at this server (built-in DNS or a router /
AdGuard rewrite) and it answers with your own station list, plus an optional
radio-browser.info catalogue. HTTPS streams and playlists are relayed as
plain HTTP, because old receivers can only play http:// MP3/AAC streams.
"""

import json
import logging
import os
import re
import socket
import socketserver
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

OPTIONS_FILE = os.environ.get("OPTIONS_FILE", "/data/options.json")
TOKEN = "0123456789ABCDEF"
RB_API = "https://all.api.radio-browser.info"
USER_AGENT = "LocalVTuner/1.0"
PAGE_DEFAULT = 100

log = logging.getLogger("vtuner")

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DEFAULTS = {
    "stations": [],
    "radio_browser": True,
    "radio_browser_country": "",
    "dns_server": True,
    "dns_upstream": "1.1.1.1",
    "host_ip": "",
    "http_port": 80,
    "dns_port": 53,
    "transliterate": False,
    "log_level": "info",
}


def load_options():
    opts = dict(DEFAULTS)
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as f:
            opts.update({k: v for k, v in json.load(f).items() if v is not None})
    except FileNotFoundError:
        log.warning("%s not found, using defaults", OPTIONS_FILE)
    for key in ("http_port", "dns_port"):
        env = os.environ.get(key.upper())
        if env:
            opts[key] = int(env)
    return opts


def detect_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


OPTS = load_options()
HOST_IP = OPTS["host_ip"].strip() or detect_ip()
BASE = "http://%s%s" % (HOST_IP, "" if OPTS["http_port"] == 80 else ":%d" % OPTS["http_port"])

# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e",
    "є": "ye", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def label(text):
    text = (text or "").strip()
    if OPTS["transliterate"]:
        out = []
        for ch in text:
            low = ch.lower()
            if low in _TR:
                t = _TR[low]
                out.append(t.capitalize() if ch != low and t else t)
            else:
                out.append(ch)
        text = "".join(out)
    return text[:60]


def x(text):
    return escape(str(text if text is not None else ""))


# --------------------------------------------------------------------------
# vTuner XML
# --------------------------------------------------------------------------


def with_dummy(url):
    # Receivers blindly append "&mac=...&dlang=..." to every URL, so each
    # URL we hand out must already contain a "?".
    return url if "?" in url else url + "?vt=1"


def dir_item(title, path, count=-1):
    url = with_dummy(BASE + path)
    return (
        "<Item><ItemType>Dir</ItemType><Title>%s</Title><UrlDir>%s</UrlDir>"
        "<UrlDirBackUp>%s</UrlDirBackUp><DirCount>%d</DirCount></Item>"
        % (x(label(title)), x(url), x(url), count)
    )


def station_item(st):
    return (
        "<Item><ItemType>Station</ItemType><StationId>%s</StationId>"
        "<StationName>%s</StationName><StationUrl>%s</StationUrl>"
        "<StationDesc>%s</StationDesc><Logo></Logo><StationFormat>%s</StationFormat>"
        "<StationLocation>%s</StationLocation><StationBandWidth>%s</StationBandWidth>"
        "<StationMime>%s</StationMime><Relia>3</Relia><Bookmark></Bookmark></Item>"
        % (
            x(st["id"]), x(label(st["name"])), x(play_url(st)),
            x(label(st.get("desc", ""))), x(label(st.get("genre", ""))),
            x(label(st.get("location", ""))), x(st.get("bitrate") or 128),
            x(st.get("mime") or "MP3"),
        )
    )


def display_item(text):
    return "<Item><ItemType>Display</ItemType><Display>%s</Display></Item>" % x(label(text))


def page(items, total=None, no_cache=True):
    total = len(items) if total is None else total
    body = "".join(items)
    nc = "<NoDataCache>Yes</NoDataCache>" if no_cache else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        "<ListOfItems><ItemCount>%d</ItemCount>%s%s</ListOfItems>" % (total, nc, body)
    )


def paged(items, query):
    """Apply the receiver's startitems/enditems (1-based, inclusive)."""
    total = len(items)
    try:
        start = max(int(query.get("startitems", 1)), 1)
    except ValueError:
        start = 1
    try:
        end = int(query.get("enditems", start + PAGE_DEFAULT - 1))
    except ValueError:
        end = start + PAGE_DEFAULT - 1
    return page(items[start - 1:end], total)


# --------------------------------------------------------------------------
# stations
# --------------------------------------------------------------------------

PLAYLIST_RE = re.compile(r"\.(m3u|pls)(\?|$)", re.I)


def guess_mime(url, codec=""):
    c = (codec or "").upper()
    if "AAC" in c or re.search(r"\.(aac|m4a)(\?|$)", url, re.I):
        return "AAC"
    if "WMA" in c:
        return "WMA"
    return "MP3"


def needs_proxy(url):
    return url.lower().startswith("https://") or bool(PLAYLIST_RE.search(url))


def play_url(st):
    if needs_proxy(st["url"]):
        return "%s/vt/stream?id=%s" % (BASE, urllib.parse.quote(st["id"]))
    return st["url"]


def my_stations():
    result = []
    for i, s in enumerate(OPTS.get("stations") or []):
        url = (s.get("url") or "").strip()
        if not url:
            continue
        result.append({
            "id": "MS_%d" % i,
            "name": s.get("name") or url,
            "url": url,
            "folder": (s.get("folder") or "").strip(),
            "mime": guess_mime(url),
            "desc": "",
            "genre": s.get("folder") or "",
        })
    return result


# --------------------------------------------------------------------------
# radio-browser.info
# --------------------------------------------------------------------------

_rb_cache = {}
_rb_lock = threading.Lock()
_rb_station_cache = {}


def rb_get(path, ttl=3600):
    now = time.time()
    with _rb_lock:
        hit = _rb_cache.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    req = urllib.request.Request(RB_API + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    with _rb_lock:
        _rb_cache[path] = (now, data)
    return data


def rb_station(d):
    url = d.get("url_resolved") or d.get("url") or ""
    st = {
        "id": "RB_" + d.get("stationuuid", ""),
        "name": d.get("name", "").strip(),
        "url": url,
        "mime": guess_mime(url, d.get("codec")),
        "bitrate": d.get("bitrate") or 128,
        "genre": (d.get("tags") or "").split(",")[0],
        "location": d.get("country", ""),
        "desc": "",
    }
    _rb_station_cache[st["id"]] = st
    return st


def rb_list(path):
    stations = []
    for d in rb_get(path):
        codec = (d.get("codec") or "").upper()
        if codec and not any(c in codec for c in ("MP3", "AAC")):
            continue  # skip codecs the receiver can't decode
        if d.get("hls"):
            continue
        stations.append(rb_station(d))
    return stations


def rb_station_by_id(sid):
    if sid in _rb_station_cache:
        return _rb_station_cache[sid]
    data = rb_get("/json/stations/byuuid/" + urllib.parse.quote(sid[3:]), ttl=600)
    return rb_station(data[0]) if data else None


STATION_FILTER = "hidebroken=true&order=clickcount&reverse=true&limit=200"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def find_station(sid):
    if not sid:
        return None
    if sid.startswith("MS_"):
        for st in my_stations():
            if st["id"] == sid:
                return st
        return None
    if sid.startswith("RB_"):
        return rb_station_by_id(sid)
    return None


def resolve_playlist(url, depth=0):
    """Return the first stream URL from an .m3u/.pls playlist (or url itself)."""
    if depth > 3 or not PLAYLIST_RE.search(url):
        return url
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read(65536).decode("utf-8", "replace")
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.lower().startswith(("http://", "https://")):
            return resolve_playlist(line, depth + 1)
    return url


class Handler(BaseHTTPRequestHandler):
    server_version = "Microsoft-IIS/6.0"  # what the real vTuner reported
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---- helpers
    def send_xml(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, body, ctype="text/html; charset=utf-8", code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.do_GET()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.lower().rstrip("/") or "/"
        query = {k.lower(): v for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)}
        log.info("%s -> %s %s", self.client_address[0], self.command, self.path)
        try:
            self.route(path, query)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # never leave the receiver hanging
            if isinstance(e, OSError):
                log.warning("network error on %s: %s", self.path, e)
                e = "network"
            else:
                log.exception("error handling %s", self.path)
            try:
                self.send_xml(page([display_item("Error: %s" % e)]))
            except Exception:
                pass

    def route(self, path, q):
        if path == "/":
            return self.status_page()
        if path.startswith("/setupapp/"):
            name = path.rsplit("/", 1)[-1]
            if name == "loginxml.asp":
                if q.get("token") == "0":
                    return self.send_xml("<EncryptedToken>%s</EncryptedToken>" % TOKEN)
                return self.root_menu(q)
            if name == "statxml.asp":
                return self.station_info(q)
            if name == "search.asp":
                if q.get("ssearchtype") == "3" and q.get("search"):
                    return self.station_info({"id": q["search"]})
                return self.search(q)
            if name == "navxml.asp":
                return self.rb_root(q)
            if name == "favxml.asp":
                return self.my_root(q)
            log.warning("unhandled vTuner request: %s (send this line to get it supported)", self.path)
            return self.root_menu(q)
        if path == "/vt/my":
            return self.my_root(q)
        if path == "/vt/my/folder":
            return self.my_folder(q)
        if path == "/vt/rb":
            return self.rb_root(q)
        if path == "/vt/rb/top":
            return self.send_xml(paged([station_item(s) for s in rb_list("/json/stations/topclick/100")], q))
        if path == "/vt/rb/country":
            return self.rb_country(q)
        if path == "/vt/rb/countries":
            return self.rb_countries(q)
        if path == "/vt/rb/tags":
            return self.rb_tags(q)
        if path == "/vt/rb/tag":
            return self.rb_tag(q)
        if path == "/vt/stream":
            return self.stream(q)
        self.send_text("not found", "text/plain", 404)

    # ---- menus
    def root_menu(self, q):
        items = [dir_item("My Stations", "/vt/my", len(my_stations()))]
        if OPTS["radio_browser"]:
            items.append(dir_item("Radio Browser", "/vt/rb"))
        self.send_xml(paged(items, q))

    def my_root(self, q):
        stations = my_stations()
        folders = []
        for st in stations:
            if st["folder"] and st["folder"] not in folders:
                folders.append(st["folder"])
        items = [
            dir_item(f, "/vt/my/folder?name=" + urllib.parse.quote(f),
                     sum(1 for s in stations if s["folder"] == f))
            for f in folders
        ]
        items += [station_item(s) for s in stations if not s["folder"]]
        if not items:
            items = [display_item("No stations configured")]
        self.send_xml(paged(items, q))

    def my_folder(self, q):
        name = q.get("name", "")
        items = [station_item(s) for s in my_stations() if s["folder"] == name]
        self.send_xml(paged(items or [display_item("Empty")], q))

    def rb_root(self, q):
        if not OPTS["radio_browser"]:
            return self.my_root(q)
        items = []
        country = OPTS["radio_browser_country"].strip()
        if country:
            items.append(dir_item(country, "/vt/rb/country?name=" + urllib.parse.quote(country)))
        items += [
            dir_item("Popular", "/vt/rb/top"),
            dir_item("Countries", "/vt/rb/countries"),
            dir_item("Genres", "/vt/rb/tags"),
        ]
        self.send_xml(paged(items, q))

    def rb_country(self, q):
        name = q.get("name", "")
        path = "/json/stations/bycountry/%s?%s" % (urllib.parse.quote(name), STATION_FILTER)
        items = [station_item(s) for s in rb_list(path)]
        self.send_xml(paged(items or [display_item("Nothing found")], q))

    def rb_countries(self, q):
        data = rb_get("/json/countries?order=stationcount&reverse=true&hidebroken=true")
        items = [
            dir_item(c["name"], "/vt/rb/country?name=" + urllib.parse.quote(c["name"]), c.get("stationcount", -1))
            for c in data[:80] if c.get("name")
        ]
        self.send_xml(paged(items, q))

    def rb_tags(self, q):
        data = rb_get("/json/tags?order=stationcount&reverse=true&hidebroken=true&limit=80")
        items = [
            dir_item(t["name"], "/vt/rb/tag?name=" + urllib.parse.quote(t["name"]), t.get("stationcount", -1))
            for t in data if t.get("name")
        ]
        self.send_xml(paged(items, q))

    def rb_tag(self, q):
        path = "/json/stations/bytagexact/%s?%s" % (urllib.parse.quote(q.get("name", "")), STATION_FILTER)
        items = [station_item(s) for s in rb_list(path)]
        self.send_xml(paged(items or [display_item("Nothing found")], q))

    def search(self, q):
        term = q.get("search", "").strip()
        if not term:
            return self.send_xml(page([display_item("Empty search")]))
        term_l = term.lower()
        items = [station_item(s) for s in my_stations() if term_l in s["name"].lower()]
        if OPTS["radio_browser"]:
            path = "/json/stations/byname/%s?%s" % (urllib.parse.quote(term), STATION_FILTER)
            items += [station_item(s) for s in rb_list(path)]
        self.send_xml(paged(items or [display_item("Nothing found")], q))

    def station_info(self, q):
        st = find_station(q.get("id", ""))
        if not st:
            return self.send_xml(page([display_item("Station not found")]))
        self.send_xml(page([station_item(st)]))

    # ---- stream relay (https / playlists -> plain http)
    def stream(self, q):
        st = find_station(q.get("id", ""))
        if not st:
            return self.send_text("unknown station", "text/plain", 404)
        url = resolve_playlist(st["url"])
        log.info("relaying %s -> %s", st["name"], url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Icy-MetaData": "0"})
        ctx = ssl.create_default_context()
        upstream = urllib.request.urlopen(req, timeout=20, context=ctx)
        try:
            ctype = upstream.headers.get("Content-Type", "audio/mpeg").split(";")[0]
            if not ctype.startswith("audio"):
                ctype = "audio/aac" if st.get("mime") == "AAC" else "audio/mpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-cache")
            name = upstream.headers.get("icy-name")
            if name:
                self.send_header("icy-name", name)
            self.end_headers()
            self.close_connection = True
            if self.command == "HEAD":
                return
            while True:
                chunk = upstream.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
        finally:
            upstream.close()
            log.info("relay stopped: %s", st["name"])

    # ---- status page for the browser
    def status_page(self):
        rows = "".join(
            "<tr><td>%s</td><td>%s</td><td><a href='%s'>%s</a></td></tr>"
            % (x(s["folder"]), x(s["name"]), x(play_url(s)),
               "relay" if needs_proxy(s["url"]) else "direct")
            for s in my_stations()
        )
        html = (
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Local vTuner</title><style>body{font-family:sans-serif;margin:16px}"
            "td,th{padding:4px 8px;border-bottom:1px solid #ccc;text-align:left}</style>"
            "<h2>Local vTuner работает</h2>"
            "<p>IP сервера: <b>%s</b>. DNS-сервер: <b>%s</b>.</p>"
            "<p>Направьте <code>pioneer.vtuner.com</code> на этот IP "
            "(или укажите %s как DNS в настройках сети ресивера).</p>"
            "<p><a href='/setupapp/pioneer/asp/BrowseXML/loginXML.asp?vt=1'>Проверить меню как ресивер</a></p>"
            "<table><tr><th>Папка</th><th>Станция</th><th>Поток</th></tr>%s</table>"
            % (x(HOST_IP), "включён" if OPTS["dns_server"] else "выключен", x(HOST_IP), rows)
        )
        self.send_text(html)


# --------------------------------------------------------------------------
# DNS: answer *.vtuner.com with our IP, forward everything else
# --------------------------------------------------------------------------

REDIRECT_SUFFIXES = (".vtuner.com", "vtuner.com", ".radiosetup.com", ".vtuner.net")


def parse_qname(data):
    labels, i = [], 12
    while i < len(data):
        n = data[i]
        if n == 0:
            i += 1
            break
        labels.append(data[i + 1:i + 1 + n].decode("ascii", "replace"))
        i += 1 + n
    qtype, = struct.unpack("!H", data[i:i + 2])
    return ".".join(labels).lower(), qtype, i + 4


def build_answer(query, end, qtype, ip):
    tid = query[:2]
    flags = struct.pack("!H", 0x8180)  # response, RD, RA, NOERROR
    question = query[12:end]
    if qtype in (1, 255):  # A / ANY
        counts = struct.pack("!HHHH", 1, 1, 0, 0)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(ip)
    else:  # e.g. AAAA -> empty answer so the receiver falls back to IPv4
        counts = struct.pack("!HHHH", 1, 0, 0, 0)
        answer = b""
    return tid + flags + counts + question + answer


class DNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        try:
            name, qtype, end = parse_qname(data)
        except Exception:
            return
        if name.endswith(REDIRECT_SUFFIXES):
            log.info("DNS %s -> %s (local vTuner)", name, HOST_IP)
            sock.sendto(build_answer(data, end, qtype, HOST_IP), self.client_address)
            return
        up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        up.settimeout(5)
        try:
            up.sendto(data, (OPTS["dns_upstream"], 53))
            resp, _ = up.recvfrom(4096)
            sock.sendto(resp, self.client_address)
        except OSError as e:
            log.debug("DNS forward failed for %s: %s", name, e)
        finally:
            up.close()


class ThreadingUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    daemon_threads = True
    allow_reuse_address = True


# --------------------------------------------------------------------------


def main():
    logging.basicConfig(
        level=getattr(logging, str(OPTS["log_level"]).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log.info("Local vTuner: http on %s, %d station(s) configured", BASE, len(my_stations()))
    if OPTS["dns_server"]:
        dns = ThreadingUDPServer(("0.0.0.0", OPTS["dns_port"]), DNSHandler)
        threading.Thread(target=dns.serve_forever, daemon=True).start()
        log.info("DNS on port %d: *.vtuner.com -> %s, rest -> %s",
                 OPTS["dns_port"], HOST_IP, OPTS["dns_upstream"])
    httpd = ThreadingHTTPServer(("0.0.0.0", OPTS["http_port"]), Handler)
    httpd.daemon_threads = True
    httpd.serve_forever()


if __name__ == "__main__":
    main()
