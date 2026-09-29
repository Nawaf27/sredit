# sredit

A small script that downloads posts and comments from a subreddit and saves them as CSV files.

It tries Reddit's own `.json` endpoints first. If Reddit blocks the request (HTTP 403), it falls back to the public [Arctic Shift](https://arctic-shift.photon-reddit.com) archive, using Reddit's RSS feeds to keep the hot / top / rising order.

## Requirements

- Python 3.9+
- `requests` and `pandas`

```
pip install requests pandas
```

## Usage

### Interactive

```
python sredit.py
```

The script asks for:

| Question | Default | Options |
|---|---|---|
| Subreddit name | `SaudiForSaudis` | any subreddit (with or without `r/`) |
| How many posts | `100` | any number |
| Sort by | `new` | `new`, `hot`, `top`, `rising` |
| Top of (only for `top`) | `week` | `hour`, `day`, `week`, `month`, `year`, `all` |
| Posts | `visible` | `visible` (shown on the subreddit only), `all` (including removed/deleted) |

### Command line

```
python sredit.py <subreddit> <num_posts> <sort> [period] [--all]
```

Examples:

```
python sredit.py SaudiProfessionals 100 new
python sredit.py SaudiProfessionals 50 top month
python sredit.py SaudiProfessionals 100 new --all
```

Without `--all`, only visible posts are saved.

## Output

Each subreddit gets its own folder under `data/`:

```
data/<subreddit>/
├── posts.csv
├── comments.csv
└── users.csv
```

**posts.csv**: `post_title`, `post_author`, `post_text`, `date`, `score`, `num_comments`, `url`, `status`

- `status` is `visible`, `removed` (removed by moderators or Reddit's filters) or `deleted` (deleted by the author).

**comments.csv**: `post_title`, `username`, `is_post_author`, `reply_to`, `comment`, `date`, `post_url`

**users.csv**: `username`, `posts`, `comments`, `total`, which counts each user's posts and comments across all saved data, sorted by total.

Dates are in Saudi time (UTC+3). Files are written as UTF-8 with BOM, so Arabic text opens correctly in Excel.

## Notes

- Running the script again on the same subreddit adds new posts and refreshes posts that were already saved (their comments are replaced with the fresh copy).
- Pinned posts are skipped.
- Collapsed comment threads ("load more comments") are skipped when reading from Reddit directly.
- Removed/deleted status comes from the archive. A post removed after the archive last updated it can still show as `visible`.
