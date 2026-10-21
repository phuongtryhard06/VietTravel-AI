import os
import re
import urllib.parse
import unicodedata
import requests
import psycopg2
from typing import Optional, Dict, Any, List

def remove_accents(input_str: str) -> str:
    """Loại bỏ dấu tiếng Việt để phục vụ tìm kiếm từ khóa linh hoạt."""
    if not input_str:
        return ""
    nfkd = unicodedata.normalize('NFKD', input_str)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).replace('đ', 'd').replace('Đ', 'D').lower()

class ImageCrawlerAgent:
    """
    Tác tử Cào Dữ Liệu Ảnh Thực Tế Thời Gian Thực (100% Dynamic Real-time Image Crawler Agent).
    Tuyệt đối không lưu trữ hay hardcode đường dẫn ảnh mẫu tĩnh.
    Khi người dùng hỏi, Agent sẽ tự động cào ảnh thực tế trực tiếp từ:
    1. Bách khoa toàn thư Wikipedia PageImages API (vi.wikipedia & en.wikipedia)
    2. Kho dữ liệu nhiếp ảnh mở Wikimedia Commons Search API
    3. Bộ máy tìm kiếm hình ảnh thực tế Bing Images Search Scraper
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en;q=0.9"
        })
        self._memory_cache: Dict[str, str] = {}

    def crawl_image(self, name: str, category: str = "", region: str = "", osm_id: Optional[str] = None) -> str:
        """
        Điểm điều phối cào ảnh động: Nhận tên địa danh/quán ăn/danh thắng từ người dùng,
        tiến hành cào live trực tiếp từ internet và trả về URL ảnh thật tương ứng.
        """
        clean_name = (name or "").strip()
        clean_reg = (region or "").strip()

        if not clean_name:
            return ""

        cache_key = f"{clean_name}::{clean_reg}".lower()
        if cache_key in self._memory_cache and self._memory_cache[cache_key]:
            return self._memory_cache[cache_key]

        # Tách các địa danh đơn lẻ nếu người dùng hoặc AI ghép nhiều địa danh bằng dấu phẩy/ngoặc đơn
        candidates = self._extract_search_candidates(clean_name, clean_reg)

        crawled_url = ""
        for cand in candidates:
            # 1. Thử Wikipedia tiếng Việt / tiếng Anh
            crawled_url = self._crawl_wikipedia(cand)
            if crawled_url:
                break

            # 2. Thử Wikimedia Commons ảnh thực địa
            crawled_url = self._crawl_wikimedia_commons(cand)
            if not crawled_url and clean_reg and cand != clean_reg:
                crawled_url = self._crawl_wikimedia_commons(f"{cand} {clean_reg}")
            if crawled_url:
                break

            # 3. Thử Bing Images Web Scraper
            search_query = f"{cand} {clean_reg} du lịch phong cảnh".strip()
            crawled_url = self._crawl_search_engine(search_query)
            if not crawled_url:
                crawled_url = self._crawl_search_engine(f"{cand} {clean_reg}".strip())
            if not crawled_url:
                crawled_url = self._crawl_search_engine(cand)
            if crawled_url:
                break

        # 4. Nếu vẫn chưa có nhưng có khu vực cấp tỉnh/huyện thì cào ảnh của khu vực đó
        if not crawled_url and clean_reg and clean_reg != clean_name:
            crawled_url = self.crawl_image(clean_reg, "", "")

        # Lưu vào cache & CSDL để tái sử dụng
        if crawled_url:
            self._memory_cache[cache_key] = crawled_url
            self._persist_to_db(osm_id, crawled_url)
            return crawled_url

        return ""

    def _extract_search_candidates(self, name: str, region: str) -> List[str]:
        """Tách nhỏ chuỗi ghép nhiều địa danh thành các tên điểm đến độc lập."""
        clean = re.sub(r'\(.*?\)', '', name).strip()
        parts = [p.strip() for p in re.split(r'[,;&/]', clean) if p.strip()]
        candidates = []
        for p in parts:
            if len(p) >= 3 and p not in candidates:
                candidates.append(p)
        if clean and clean not in candidates:
            candidates.append(clean)
        if name not in candidates:
            candidates.append(name)
        return candidates

    def _crawl_wikipedia(self, title: str) -> str:
        """Cào ảnh đại diện chính thức từ Wikipedia tiếng Việt hoặc tiếng Anh kèm xác thực tiêu đề."""
        q_words = [w for w in remove_accents(title).split() if len(w) >= 3]
        for lang in ["vi", "en"]:
            try:
                url = f"https://{lang}.wikipedia.org/w/api.php"
                # 1. Thử khớp chính xác tiêu đề
                params = {
                    "action": "query",
                    "prop": "pageimages",
                    "format": "json",
                    "piprop": "thumbnail",
                    "pithumbsize": 1000,
                    "titles": title.strip(),
                    "redirects": 1,
                    "origin": "*"
                }
                r = self.session.get(url, params=params, timeout=5)
                if r.status_code == 200:
                    pages = r.json().get("query", {}).get("pages", {})
                    for pid, pdata in pages.items():
                        if pid != "-1":
                            thumb = pdata.get("thumbnail", {}).get("source")
                            if thumb and not any(bad in thumb.lower() for bad in [
                                ".svg", ".pdf", ".djvu", "panoramio", "bantho", "ban-tho", "altar", 
                                "shrine", "statue", "tomb", "grave", "cemetery", "nghia-trang"
                            ]):
                                return thumb

                # 2. Thử generator=search với xác thực từ khóa tiêu đề bài viết
                search_params = {
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": title.strip(),
                    "gsrlimit": 3,
                    "prop": "pageimages",
                    "piprop": "thumbnail",
                    "pithumbsize": 1000,
                    "format": "json",
                    "redirects": 1,
                    "origin": "*"
                }
                r2 = self.session.get(url, params=search_params, timeout=5)
                if r2.status_code == 200:
                    pages2 = r2.json().get("query", {}).get("pages", {})
                    for pid, pdata in pages2.items():
                        t_clean = remove_accents(pdata.get("title", ""))
                        if any(w in t_clean for w in q_words):
                            thumb = pdata.get("thumbnail", {}).get("source")
                            if thumb and not any(bad in thumb.lower() for bad in [
                                ".svg", ".pdf", ".djvu", "panoramio", "bantho", "ban-tho", "altar", 
                                "shrine", "statue", "tomb", "grave", "cemetery", "nghia-trang"
                            ]):
                                return thumb
            except Exception:
                pass
        return ""

    def crawl_destination_hero_image(self, destination: str) -> str:
        """
        Cào ảnh biểu tượng toàn cảnh đẹp nhất (Hero Landscape) của điểm đến hoặc tỉnh/thành phố.
        Ưu tiên ảnh phong cảnh thiên nhiên, danh thắng tiêu biểu, tuyệt đối không lấy ảnh trong nhà, điện thờ.
        """
        dest_clean = (destination or "").strip()
        if not dest_clean:
            return ""

        cache_key = f"hero::{dest_clean}".lower()
        if cache_key in self._memory_cache and self._memory_cache[cache_key]:
            return self._memory_cache[cache_key]

        # 1. Thử Wikipedia tiêu đề chính hoặc kèm chữ (tỉnh)
        for t in [dest_clean, f"{dest_clean} (tỉnh)", f"Du lịch {dest_clean}"]:
            img = self._crawl_wikipedia(t)
            if img:
                self._memory_cache[cache_key] = img
                return img

        # 2. Thử Wikimedia Commons với từ khóa phong cảnh danh thắng
        for q in [f"{dest_clean} landscape", f"{dest_clean} scenery", f"du lịch {dest_clean}"]:
            img = self._crawl_wikimedia_commons(q)
            if img:
                self._memory_cache[cache_key] = img
                return img

        # 3. Fallback sang cào chuẩn
        img = self.crawl_image(dest_clean, "Danh lam thắng cảnh", dest_clean)
        if img:
            self._memory_cache[cache_key] = img
            return img

        return ""

    def _crawl_wikimedia_commons(self, query: str) -> str:
        """Cào ảnh thực tế từ kho lưu trữ đa phương tiện mở Wikimedia Commons."""
        try:
            url = "https://commons.wikimedia.org/w/api.php"
            # Ưu tiên tìm kiếm kèm từ khóa phong cảnh/du lịch để lấy ảnh thiên nhiên danh thắng
            search_variations = [
                f"{query.strip()} landscape",
                f"{query.strip()} scenery",
                f"{query.strip()} du lịch",
                f'"{query.strip()}"',
                query.strip()
            ]
            for sv in search_variations:
                params = {
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": sv,
                    "gsrnamespace": 6,
                    "gsrlimit": 8,
                    "prop": "imageinfo",
                    "iiprop": "url|mime",
                    "iiurlwidth": 1200,
                    "format": "json",
                    "origin": "*"
                }
                r = self.session.get(url, params=params, timeout=5)
                if r.status_code == 200:
                    pages = r.json().get("query", {}).get("pages", {})
                    for pid, pdata in pages.items():
                        title = pdata.get("title", "")
                        t_norm = remove_accents(title)

                        # Bỏ qua tệp đồ họa vector, bản đồ, tài liệu scan pdf, logo, cờ hiệu, ban thờ, bia mộ, ảnh panoramio tự phát
                        bad_keywords = [
                            ".svg", ".pdf", ".djvu", "flag", "coat of arms", "map", "ban do", "logo", "icon", 
                            "seal", "emblem", "ban mau", "ban tho", "altar", "tomb", "grave", "cemetery", 
                            "nghia trang", "tang le", "funeral", "panoramio", "hoanh phi", "cau doi", 
                            "tuong", "statue", "shrine", "tho cung", "dien tho", "chua", "den tho",
                            "bat huong", "tam phu", "tu phu", "cung le"
                        ]
                        if any(bad in t_norm for bad in bad_keywords):
                            continue

                        infos = pdata.get("imageinfo", [])
                        if infos:
                            thumb = infos[0].get("thumburl") or infos[0].get("url")
                            mime = infos[0].get("mime", "")
                            if thumb and "image" in mime:
                                u_norm = remove_accents(urllib.parse.unquote(thumb))
                                if any(bad in u_norm for bad in bad_keywords):
                                    continue
                                if not any(ext in thumb.lower() for ext in [".svg", ".pdf", "page1-", ".djvu"]):
                                    return thumb
        except Exception:
            pass
        return ""

    def _crawl_search_engine(self, query: str) -> str:
        """Cào ảnh trực tiếp từ bộ máy tìm kiếm hình ảnh thực tế (Bing Images Search)."""
        try:
            encoded = urllib.parse.quote(query.strip())
            url = f"https://www.bing.com/images/search?q={encoded}&first=1"
            r = self.session.get(url, timeout=6)
            if r.status_code == 200:
                html = r.text
                murls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', html) or re.findall(r'\"murl\":\"(https?://[^\"]+)\"', html)
                for u in murls:
                    u_lower = u.lower()
                    # Lọc bỏ ảnh pixel theo dõi, icon nhỏ, tệp svg, hoặc ảnh ban thờ/nghĩa trang
                    if any(bad in u_lower for bad in [
                        ".svg", ".gif", "avatar", "icon", "pixel", "1x1", "blank", "default", "tracker",
                        "ban-tho", "bantho", "altar", "cemetery", "nghia-trang"
                    ]):
                        continue
                    if any(ext in u_lower for ext in [".jpg", ".jpeg", ".png", ".webp"]) or "http" in u_lower:
                        return u
        except Exception:
            pass
        return ""

    def _persist_to_db(self, osm_id: Optional[str], image_url: str):
        """Tự động cập nhật ảnh đã cào vào CSDL osm_attractions_staging để tái sử dụng."""
        if not osm_id or not image_url:
            return
        db_url = os.getenv("DATABASE_POSTGRESQL_URL")
        if not db_url:
            return
        try:
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute(
                "UPDATE osm_attractions_staging SET image_url = %s WHERE (osm_id = %s OR id::text = %s) AND (image_url IS NULL OR image_url = '');",
                (image_url, str(osm_id), str(osm_id))
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass

image_crawler_agent = ImageCrawlerAgent()
