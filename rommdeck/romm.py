"""Minimal RomM REST client (standard library only)."""
import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class RommError(Exception):
    pass


class Cancelled(Exception):
    pass


class RommClient:
    def __init__(self, cfg):
        url = cfg["server_url"].strip().rstrip("/")
        if url and not re.match(r"https?://", url):
            url = "http://" + url
        if url.endswith("/api"):
            url = url[:-4]
        self.base = url
        self.headers = {"Accept": "application/json", "User-Agent": "romm-deck"}
        if cfg.get("api_token"):
            self.headers["Authorization"] = "Bearer " + cfg["api_token"].strip()
        elif cfg.get("username"):
            raw = f"{cfg['username']}:{cfg.get('password', '')}".encode()
            self.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        self.ctx = None
        if not cfg.get("verify_tls", True):
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    # -- low level ---------------------------------------------------------
    def _open(self, path, params=None, timeout=30):
        if not self.base:
            raise RommError("No RomM server configured. Open Settings first.")
        url = path if path.startswith("http") else self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        # Only send credentials to the RomM server itself
        headers = self.headers if url.startswith(self.base) else {"User-Agent": "romm-deck"}
        req = urllib.request.Request(url, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=self.ctx)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RommError(f"RomM refused the login (HTTP {e.code}). Check your username and password in Settings.")
            raise RommError(f"RomM returned HTTP {e.code} for {path}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RommError(f"Could not reach RomM at {self.base}: {getattr(e, 'reason', e)}")

    def _json(self, path, params=None):
        with self._open(path, params) as r:
            try:
                return json.loads(r.read().decode("utf-8"))
            except ValueError:
                raise RommError("Unexpected (non-JSON) reply. Is the server URL right?")

    # -- API -----------------------------------------------------------------
    def platforms(self):
        plats = [p for p in self._json("/api/platforms") if p.get("rom_count", 1)]
        plats.sort(key=lambda p: platform_name(p).lower())
        return plats

    def roms(self, platform_id=None, search="", limit=60, offset=0):
        """Return (items, total)."""
        params = {"limit": limit, "offset": offset, "order_by": "name", "order_dir": "asc",
                  # skip the big index/filter blobs RomM 4+ attaches by default
                  "with_char_index": "false", "with_filter_values": "false",
                  "with_rom_id_index": "false"}
        if platform_id is not None:
            params["platform_ids"] = platform_id   # RomM 4.x
            params["platform_id"] = platform_id    # RomM 3.x
        if search:
            params["search_term"] = search
        data = self._json("/api/roms", params)
        if isinstance(data, list):                 # very old servers: no pagination
            return data, len(data)
        items = data.get("items", [])
        total = data.get("total")
        return items, (total if total is not None else offset + len(items))

    def cover(self, rom):
        """Return cover image bytes or None."""
        path = rom.get("path_cover_small") or rom.get("path_cover_large")
        candidates = []
        if path:
            if not path.startswith("/assets/"):      # RomM 5 already includes the prefix
                path = "/assets/romm/resources/" + path.lstrip("/")
            candidates.append(urllib.parse.quote(path, safe="/?=&"))
        if rom.get("url_cover"):
            candidates.append(rom["url_cover"])
        for c in candidates:
            try:
                with self._open(c, timeout=20) as r:
                    return r.read()
            except RommError:
                continue
        return None

    def platform_logo(self, platform):
        """Return (bytes) of the console logo RomM ships, or None."""
        for name in (platform.get("slug"), platform.get("fs_slug")):
            for ext in ("svg", "ico"):
                try:
                    with self._open(f"/assets/platforms/{urllib.parse.quote(name or '')}.{ext}",
                                    timeout=20) as r:
                        return r.read()
                except RommError:
                    continue
        return None

    def download(self, rom, dest_dir, progress=None, cancelled=lambda: False):
        """Download a rom into dest_dir. Returns the path of the file or folder
        created. progress(done_bytes, total_bytes) is called as data arrives."""
        os.makedirs(dest_dir, exist_ok=True)
        fs_name = safe_name(rom["fs_name"])
        multi = bool(rom.get("has_multiple_files", rom.get("multi")))
        url = f"/api/roms/{rom['id']}/content/{urllib.parse.quote(rom['fs_name'])}"
        part = os.path.join(dest_dir, f".{fs_name}.rommdeck-part")
        try:
            with self._open(url, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0) or rom.get("fs_size_bytes") or 0
                sent_name = safe_name(r.headers.get_filename() or "")
                done = 0
                with open(part, "wb") as f:
                    while True:
                        if cancelled():
                            raise Cancelled()
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            if multi and zipfile.is_zipfile(part):
                target = os.path.join(dest_dir, fs_name)
                extract_zip(part, target)
                os.remove(part)
                return target
            # A folder-with-one-file rom is served as that single file
            name = fs_name
            if sent_name and (rom.get("has_nested_single_file") or multi):
                name = sent_name
            target = os.path.join(dest_dir, name)
            os.replace(part, target)
            return target
        except OSError as e:
            raise RommError(f"Download failed: {e}")
        finally:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass


def platform_name(p):
    return p.get("display_name") or p.get("custom_name") or p.get("name") or p.get("slug", "?")


def safe_name(name):
    """Strip any directory components from a server-supplied file name."""
    name = os.path.basename(name.replace("\\", "/")).strip()
    return "" if name in (".", "..") else name


def extract_zip(zip_path, target):
    os.makedirs(target, exist_ok=True)
    root = os.path.realpath(target)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            out = os.path.realpath(os.path.join(root, info.filename))
            if out != root and not out.startswith(root + os.sep):
                raise RommError(f"Unsafe path in archive: {info.filename}")
        z.extractall(root)
