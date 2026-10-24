import os
import re
import sys
import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

# Each subreddit gets its own folder: data/<subreddit>/posts.csv, comments.csv, users.csv
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Reddit asks scripts to identify themselves instead of posing as a browser
HEADERS = {"User-Agent": "sredit/1.0 by Nawaf27 (https://github.com/Nawaf27/sredit)"}
REDDIT = "https://www.reddit.com"
# Public Reddit archive, used when Reddit blocks the .json endpoints (403)
ARCHIVE = "https://arctic-shift.photon-reddit.com/api"

SORTS = ["new", "hot", "top", "rising"]
SHOW = ["visible", "all"]
PERIODS = ["hour", "day", "week", "month", "year", "all"]
KSA = timezone(timedelta(hours=3))

session = requests.Session()
session.headers.update(HEADERS)


class Blocked(Exception):
    pass


def get(url, params=None, raw=False):
    for attempt in range(6):
        r = session.get(url, params=params, timeout=60)
        if r.status_code == 403:
            raise Blocked(url)
        # The archive answers 422 when a query times out on its side
        if r.status_code in (422, 429, 500, 502, 503, 504):
            wait = 30 * (attempt + 1) if r.status_code == 429 else 5 * (attempt + 1)
            print(f"  Server busy ({r.status_code}), waiting {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.text if raw else r.json()
    raise RuntimeError(f"Request failed: {url}")


def fmt_time(ts):
    return datetime.fromtimestamp(ts, tz=KSA).strftime("%Y-%m-%d %H:%M")


def post_id_from_url(url):
    m = re.search(r"/comments/([a-z0-9]+)", url)
    return m.group(1) if m else url


def post_status(p):
    # Removed by mods/Reddit filters, or deleted by the author: not shown on the subreddit.
    # The archive's second fetch (_meta.removal_type) also catches posts removed later.
    category = p.get("removed_by_category")
    text = p.get("selftext", "")
    removal = (p.get("_meta") or {}).get("removal_type")
    if category == "deleted" or text == "[deleted]" or removal == "deleted":
        return "deleted"
    if category or text == "[removed]" or removal:
        return "removed"
    return "visible"


def score_of(p):
    # The archive saves posts seconds after they are made, so the score is almost always 1.
    # It fetches each post again about 36 hours later; until then the score is left empty.
    if "retrieved_on" in p and not (p.get("_meta") or {}).get("retrieved_2nd_on"):
        return ""
    return p.get("score")


def keep_post(p, show):
    # Pinned posts are skipped; hidden posts too unless all posts were asked for
    return not p.get("stickied") and (show == "all" or post_status(p) == "visible")


def make_post(p):
    return {
        "post_title": p.get("title", ""),
        "post_author": p.get("author"),
        "post_text": p.get("selftext", ""),
        "date": fmt_time(p["created_utc"]),
        "score": score_of(p),
        "num_comments": p.get("num_comments"),
        "url": REDDIT + p.get("permalink", f"/comments/{p['id']}/"),
        "status": post_status(p)
    }


def make_comment(post, c, reply_to):
    author = c.get("author")
    return {
        "post_title": post["post_title"],
        "username": author,
        "is_post_author": "Yes" if author == post["post_author"] and author != "[deleted]" else "No",
        "reply_to": reply_to,
        "comment": c.get("body", ""),
        "date": fmt_time(c["created_utc"]),
        "post_url": post["url"]
    }


# ===== Source 1: Reddit .json =====

def json_posts(subreddit, sort, period, count, show):
    posts = []
    after = None
    while len(posts) < count:
        data = get(f"{REDDIT}/r/{subreddit}/{sort}.json", {
            "limit": 100, "after": after, "t": period, "raw_json": 1
        })["data"]
        posts += [make_post(child["data"]) for child in data["children"]
                  if keep_post(child["data"], show)]
        print(f"  Got {min(len(posts), count)} posts")
        after = data.get("after")
        if not after:
            break
        time.sleep(2)
    return posts[:count]


def json_comments(post):
    data = get(f"{REDDIT}/comments/{post_id_from_url(post['url'])}.json",
               {"limit": 500, "raw_json": 1})
    out = []

    def walk(children, parent_author):
        for child in children:
            # "more" items are collapsed comments; skip them
            if child["kind"] != "t1":
                continue
            c = child["data"]
            out.append(make_comment(post, c, parent_author))
            if c.get("replies"):
                walk(c["replies"]["data"]["children"], c.get("author"))

    walk(data[1]["data"]["children"], post["post_author"])
    time.sleep(2)
    return out


# ===== Source 2: Reddit RSS (for the hot/top/rising order) + Arctic Shift archive =====

def rss_post_ids(subreddit, sort, period, count):
    ids = []
    after = None
    # A few extra ids make up for pinned posts dropped later
    while len(ids) < count + 5:
        params = {"limit": 100, "t": period}
        if after:
            params["after"] = "t3_" + after
        xml = get(f"{REDDIT}/r/{subreddit}/{sort}/.rss", params, raw=True)
        page = re.findall(r"<id>t3_([a-z0-9]+)</id>", xml)
        if not page or page[-1] == after:
            break
        ids += [i for i in page if i not in ids]
        after = page[-1]
        print(f"  Found {min(len(ids), count)} posts")
        time.sleep(10)
    return ids


def archive_posts_by_ids(ids, count, show):
    found = {}
    for i in range(0, len(ids), 100):
        for p in get(f"{ARCHIVE}/posts/ids", {"ids": ",".join(ids[i:i + 100])})["data"]:
            if keep_post(p, show):
                found[p["id"]] = make_post(p)
    # Keep Reddit's order
    return [found[i] for i in ids if i in found][:count]


def archive_posts(subreddit, count, show):
    posts = []
    seen = set()
    before = None
    while len(posts) < count:
        params = {"subreddit": subreddit, "limit": 100, "sort": "desc"}
        if before:
            params["before"] = before
        data = get(f"{ARCHIVE}/posts/search", params)["data"]
        if not data:
            break
        for p in data:
            if p["id"] not in seen and keep_post(p, show):
                seen.add(p["id"])
                posts.append(make_post(p))
        oldest = int(data[-1]["created_utc"])
        if oldest == before:
            break
        before = oldest
        print(f"  Got {min(len(posts), count)} posts")
    return posts[:count]


def archive_comments(post):
    raw = {}
    after = None
    while True:
        params = {"link_id": post_id_from_url(post["url"]), "limit": 100, "sort": "asc"}
        if after:
            params["after"] = after
        data = get(f"{ARCHIVE}/comments/search", params)["data"]
        new = [c for c in data if c["id"] not in raw]
        if not new:
            break
        for c in new:
            raw[c["id"]] = c
        if len(data) < 100:
            break
        after = int(data[-1]["created_utc"])

    def reply_to(c):
        parent = c["parent_id"]
        if parent.startswith("t3_"):
            return post["post_author"]
        parent_comment = raw.get(parent[3:])
        return parent_comment.get("author") if parent_comment else ""

    return [make_comment(post, c, reply_to(c)) for c in raw.values()]


# ===== Saving =====

def find_folder(subreddit):
    # Subreddit names are case-insensitive, so reuse an existing folder if the case differs
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            if name.lower() == subreddit.lower():
                return os.path.join(DATA_DIR, name)
    return os.path.join(DATA_DIR, subreddit)


def load_csv(path):
    # A run where no post had comments yet leaves an empty file
    if os.path.exists(path) and os.path.getsize(path) > 3:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    return pd.DataFrame()


def build_users(posts, comments):
    post_counts = posts["post_author"].value_counts().rename("posts")
    comment_counts = comments["username"].value_counts().rename("comments") \
        if not comments.empty else pd.Series(name="comments", dtype=int)
    users = pd.concat([post_counts, comment_counts], axis=1).fillna(0).astype(int)
    users.index.name = "username"
    users = users.drop(index=["[deleted]", ""], errors="ignore")
    users["total"] = users["posts"] + users["comments"]
    return users.sort_values("total", ascending=False).reset_index()


def ask(prompt, default, choices=None):
    while True:
        value = input(f"{prompt} [{default}]: ").strip("﻿ \t").lower() or default
        if not choices or value in choices:
            return value
        print(f"  Choose one of: {', '.join(choices)}")


def main():
    # Windows uses a non-UTF-8 encoding when the output goes to a file or pipe; Arabic titles need UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    args =[a for a in sys.argv[1:] if a != "--all"]
    if len(args) > 2:
        # python sredit.py <subreddit> <num_posts> <sort> [period] [--all]
        subreddit, count, sort = args[0], args[1], args[2].lower()
        period = args[3].lower() if len(args) > 3 else "week"
        show = "all" if "--all" in sys.argv else "visible"
    else:
        subreddit = input("Subreddit name [SaudiForSaudis]: ").strip("﻿ \t") or "SaudiForSaudis"
        count = ask("How many posts", "100")
        sort = ask(f"Sort by ({' / '.join(SORTS)})", "new", SORTS)
        period = ask(f"Top of ({' / '.join(PERIODS)})", "week", PERIODS) if sort == "top" else "week"
        show = ask("Posts: visible only, or all including removed/deleted (visible / all)", "visible", SHOW)
    subreddit = subreddit.strip("﻿ \t").removeprefix("r/").strip("/")
    count = int(count)

    folder = find_folder(subreddit)
    posts_file = os.path.join(folder, "posts.csv")
    comments_file = os.path.join(folder, "comments.csv")
    users_file = os.path.join(folder, "users.csv")

    old_posts = load_csv(posts_file)
    old_comments = load_csv(comments_file)
    if old_posts.empty:
        print(f"\nNo data yet for r/{subreddit}, creating {folder}")
        saved_urls = set()
    else:
        print(f"\nFound existing data for r/{subreddit}: {len(old_posts)} posts, "
              f"{len(old_comments)} comments. New posts will be added, saved posts updated.")
        saved_urls = set(old_posts["url"])
        if not old_comments.empty and "post_url" not in old_comments:
            old_comments["post_url"] = old_comments["post_title"].map(
                dict(zip(old_posts["post_title"], old_posts["url"])))

    label = f"{sort} ({period})" if sort == "top" else sort
    kind = "posts (including removed/deleted)" if show == "all" else "visible posts"
    print(f"Fetching {count} {kind} from r/{subreddit}, sorted by {label} ...")
    try:
        posts = json_posts(subreddit, sort, period, count, show)
        fetch_comments = json_comments
        print("Source: reddit.com .json")
    except Blocked:
        print("Reddit blocked the .json request (403), using the Arctic Shift archive instead")
        if sort == "new":
            posts = archive_posts(subreddit, count, show)
        else:
            posts = archive_posts_by_ids(rss_post_ids(subreddit, sort, period, count), count, show)
        fetch_comments = archive_comments

    if not posts:
        sys.exit("No posts found. Check the subreddit name.")

    # Saved posts still waiting for their score are fetched again from the archive
    urls = {p["url"] for p in posts}
    if not old_posts.empty and "score" in old_posts:
        waiting = old_posts[(old_posts["score"] == "") & ~old_posts["url"].isin(urls)]
        if not waiting.empty:
            print(f"Updating {len(waiting)} saved posts that had no score yet ...")
            ids = [post_id_from_url(u) for u in waiting["url"]]
            refreshed = archive_posts_by_ids(ids, len(ids), "all")
            hidden = {p["url"] for p in refreshed if p["status"] != "visible"}
            if show == "visible" and hidden:
                # Removed or deleted since they were saved
                old_posts = old_posts[~old_posts["url"].isin(hidden)]
                if not old_comments.empty:
                    old_comments = old_comments[~old_comments["post_url"].isin(hidden)]
                refreshed = [p for p in refreshed if p["url"] not in hidden]
                print(f"  Dropped {len(hidden)} posts that were removed or deleted since")
            posts += refreshed

    comments = []
    for i, post in enumerate(posts, 1):
        try:
            post_comments = fetch_comments(post)
        except Blocked:
            fetch_comments = archive_comments
            post_comments = fetch_comments(post)
        # The archive's comment count is taken when the post is first saved, so use the real one
        post["num_comments"] = len(post_comments)
        comments.extend(post_comments)
        print(f"  [{i}/{len(posts)}] {len(post_comments)} comments - {post['post_title'][:60]}")

    # Saved posts that were fetched again are replaced with the fresh copy and comments
    urls = {p["url"] for p in posts}
    new_count = len(urls - saved_urls)
    if not old_posts.empty:
        old_posts = old_posts[~old_posts["url"].isin(urls)]
    if not old_comments.empty:
        old_comments = old_comments[~old_comments["post_url"].isin(urls)]
    all_posts = pd.concat([old_posts, pd.DataFrame(posts).astype(str)], ignore_index=True)
    all_comments = pd.concat([old_comments, pd.DataFrame(comments).astype(str)], ignore_index=True)
    users = build_users(all_posts, all_comments)

    os.makedirs(folder, exist_ok=True)
    all_posts.to_csv(posts_file, index=False, encoding="utf-8-sig")
    all_comments.to_csv(comments_file, index=False, encoding="utf-8-sig")
    users.to_csv(users_file, index=False, encoding="utf-8-sig")

    print("=" * 50)
    print("Done")
    print("=" * 50)
    print(f"Fetched: {len(posts)} posts ({new_count} new, {len(posts) - new_count} updated), "
          f"{len(comments)} comments")
    print(f"Total:   {len(all_posts)} posts, {len(all_comments)} comments, {len(users)} users")
    print(f"Folder:  {folder}")


if __name__ == "__main__":
    main()
