import requests
from bs4 import BeautifulSoup
import csv
import time

# ── Correct Board IDs (verified from Bitcointalk) ─────────────────────────────
BOARDS = {
    6:   "Bitcoin Discussion",
    7:   "Beginners & Help",
    159: "Scam Accusations",      # correct ID for scam accusations
    169: "Scam Accusations (Archive)",
    238: "Altcoin Discussion",
    14:  "Marketplace",
}

KEYWORDS = ['onecoin', 'centratech', 'celsius', 'ftx', 'ponzi', 'scam', 'fraud', 'hack', 'stolen', 'rug pull']

PAGES_PER_BOARD = 20
OUTPUT_FILE     = "bitcointalk_scam_data.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# ── Verify board name before scraping ─────────────────────────────────────────
def verify_board(board_id):
    """Check actual board name and sample threads."""
    url = f"https://bitcointalk.org/index.php?board={board_id}.0"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(r.text, 'html.parser')

        # Get actual board name from page title
        title = soup.title.string if soup.title else "Unknown"

        # Count topic links
        topic_links = soup.find_all(
            'a',
            href=lambda h: h and 'topic=' in str(h) and '#new' not in str(h)
        )

        print(f"  Board {board_id} → Title: '{title}' | Topic links: {len(topic_links)}")
        return len(topic_links) > 0

    except Exception as e:
        print(f"  Board {board_id} → Error: {e}")
        return False


# ── Get threads from one board page ───────────────────────────────────────────
def get_board_threads(board_id, page_num):
    url = f"https://bitcointalk.org/index.php?board={board_id}.{page_num * 40}"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)

        if response.status_code == 403:
            print(f"    403 Blocked. Waiting 60s...")
            time.sleep(60)
            return []
        if response.status_code == 429:
            print(f"    429 Rate limited. Waiting 90s...")
            time.sleep(90)
            return []
        if response.status_code != 200:
            print(f"    HTTP {response.status_code}. Skipping.")
            return []

        soup    = BeautifulSoup(response.text, 'html.parser')
        threads = []
        seen    = set()

        # Get all topic links — exclude pagination (#new, .20, .40 etc)
        all_links = soup.find_all(
            'a',
            href=lambda h: h and 'topic=' in str(h)
        )

        for link in all_links:
            href  = link.get('href', '')
            title = link.get_text(strip=True)

            # Skip pagination links (they're just numbers like "1", "2")
            if not title or title.isdigit() or len(title) < 8:
                continue

            # Skip reply links
            if '#new' in href or 'msg' in href:
                continue

            # Make absolute URL
            if not href.startswith('http'):
                href = 'https://bitcointalk.org' + href

            # Deduplicate
            if href in seen:
                continue
            seen.add(href)

            # Keep ALL threads (no keyword filter here)
            # We filter by keyword when reading post content instead
            threads.append({'title': title, 'url': href})

        return threads

    except requests.exceptions.ConnectionError:
        print(f"    Connection error. Waiting 30s...")
        time.sleep(30)
        return []
    except requests.exceptions.Timeout:
        print(f"    Timeout. Waiting 20s...")
        time.sleep(20)
        return []
    except Exception as e:
        print(f"    Error: {e}")
        return []


# ── Get posts from one thread ──────────────────────────────────────────────────
def get_thread_posts(thread_url, thread_title, board_name, writer):
    posts_written = 0

    try:
        response = requests.get(thread_url, headers=HEADERS, timeout=30)
        if response.status_code != 200:
            return 0

        soup = BeautifulSoup(response.text, 'html.parser')

        # ── Find all post content divs ────────────────────────────────────────
        post_divs = soup.find_all('div', class_='post')

        if not post_divs:
            # Alternative: find by ID pattern msg_XXXXXX
            post_divs = soup.find_all('div', id=lambda x: x and x.startswith('msg_'))

        if not post_divs:
            # Last fallback: any div with class containing 'post'
            post_divs = soup.find_all('div', class_=lambda c: c and 'post' in c)

        for post_div in post_divs:
            text = post_div.get_text(separator=' ', strip=True)
            if len(text) < 30:
                continue

            # ── Filter by keyword — only save scam-relevant posts ─────────────
            text_lower = text.lower()
            if not any(kw in text_lower for kw in KEYWORDS):
                continue

            # ── Get author ────────────────────────────────────────────────────
            author     = '[unknown]'
            author_tag = (
                post_div.find_previous('td',  class_='poster_info') or
                post_div.find_previous('div', class_='poster_info') or
                post_div.find_previous('div', class_='poster')
            )
            if author_tag:
                name_tag = author_tag.find('b') or author_tag.find('a')
                if name_tag:
                    author = name_tag.get_text(strip=True)

            # ── Get timestamp ─────────────────────────────────────────────────
            timestamp  = ''
            date_tag   = post_div.find_previous('div', class_='smalltext')
            if date_tag:
                timestamp = date_tag.get_text(strip=True)[:60]

            writer.writerow({
                'id':            abs(hash(thread_url + text[:40])),
                'board':         board_name,
                'thread_title':  thread_title,
                'thread_url':    thread_url,
                'type':          'post',
                'author':        author,
                'timestamp_utc': timestamp,
                'text':          text[:2000]
            })
            posts_written += 1

    except Exception as e:
        print(f"      Error reading thread: {e}")

    return posts_written


# ── Main ──────────────────────────────────────────────────────────────────────
def scrape_bitcointalk():
    fieldnames = [
        'id', 'board', 'thread_title', 'thread_url',
        'type', 'author', 'timestamp_utc', 'text'
    ]

    total_threads = 0
    total_posts   = 0

    # ── Verify all boards first ───────────────────────────────────────────────
    print("=== Verifying Boards ===")
    for board_id, board_name in BOARDS.items():
        verify_board(board_id)
        time.sleep(3)
    print("========================\n")

    with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for board_id, board_name in BOARDS.items():
            print(f"\n=== Board: {board_name} (ID: {board_id}) ===")

            for page in range(PAGES_PER_BOARD):
                print(f"  Page {page + 1}/{PAGES_PER_BOARD}...")

                threads = get_board_threads(board_id, page)

                if not threads:
                    print(f"    No threads found on page {page + 1}")
                    time.sleep(3)
                    continue

                print(f"    Found {len(threads)} threads — checking for keywords...")

                for thread in threads:
                    total_threads += 1

                    posts = get_thread_posts(
                        thread['url'],
                        thread['title'],
                        board_name,
                        writer
                    )

                    if posts > 0:
                        print(f"    ✓ {posts} posts saved | {thread['title'][:55]}")
                    total_posts += posts

                    time.sleep(3)   # polite delay between threads

                time.sleep(5)       # polite delay between pages

    print(f"\n{'='*50}")
    print(f"Done!")
    print(f"Total threads checked : {total_threads}")
    print(f"Total posts saved     : {total_posts}")
    print(f"Saved to              : {OUTPUT_FILE}")


if __name__ == "__main__":
    scrape_bitcointalk()