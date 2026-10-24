import os
import sys
import pandas as pd
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sredit import find_folder, load_csv

# Accounts that are not real people taking part in the discussion
IGNORE = {"", "[deleted]", "AutoModerator"}
TOP_NODES = 40


def anonymize(posts, comments):
    # User_1 is the most active user, User_2 the next, and so on
    activity = pd.concat([posts["post_author"], comments["username"]]).value_counts()
    names = [u for u in activity.index if u not in IGNORE]
    return {u: f"User_{i}" for i, u in enumerate(names, 1)}


def reply_graph(comments, alias):
    g = nx.DiGraph()
    for src, dst in zip(comments["username"], comments["reply_to"]):
        # Replies to yourself (e.g. adding to your own comment) are not interactions
        if src in IGNORE or dst in IGNORE or src == dst:
            continue
        a, b = alias[src], alias[dst]
        w = g[a][b]["weight"] + 1 if g.has_edge(a, b) else 1
        g.add_edge(a, b, weight=w)
    return g


def user_stats(g):
    if not g.number_of_nodes():
        return pd.DataFrame()
    communities = nx.community.greedy_modularity_communities(g.to_undirected(), weight="weight")
    group = {u: i for i, c in enumerate(communities, 1) for u in c}
    betweenness = nx.betweenness_centrality(g)
    stats = pd.DataFrame({
        "user": list(g.nodes),
        "replies_received": [g.in_degree(u, weight="weight") for u in g.nodes],
        "replies_sent": [g.out_degree(u, weight="weight") for u in g.nodes],
        "people_talked_to": [len(set(g.predecessors(u)) | set(g.successors(u))) for u in g.nodes],
        "betweenness": [round(betweenness[u], 4) for u in g.nodes],
        "community": [group[u] for u in g.nodes],
    })
    return stats.sort_values("replies_received", ascending=False).reset_index(drop=True)


def draw_network(g, stats, subreddit, path):
    # Only the busiest users, so the picture stays readable
    top = list(stats.assign(degree=stats["replies_received"] + stats["replies_sent"])
               .nlargest(TOP_NODES, "degree")["user"])
    sub = g.subgraph(top)
    community = dict(zip(stats["user"], stats["community"]))
    received = dict(zip(stats["user"], stats["replies_received"]))

    pos = nx.spring_layout(sub.to_undirected(), weight="weight", k=0.9, seed=7)
    fig, ax = plt.subplots(figsize=(11, 9))
    widths = [0.4 + 0.6 * d["weight"] for _, _, d in sub.edges(data=True)]
    nx.draw_networkx_edges(sub, pos, ax=ax, width=widths, alpha=0.35, edge_color="#555",
                           arrowsize=8, connectionstyle="arc3,rad=0.1")
    nx.draw_networkx_nodes(sub, pos, ax=ax, node_size=[40 + 50 * received[u] ** 0.5 for u in sub],
                           node_color=[community[u] for u in sub], cmap="tab10",
                           edgecolors="white", linewidths=1)
    labels = {u: u for u in sorted(sub, key=lambda u: -received[u])[:12]}
    nx.draw_networkx_labels(sub, pos, labels, ax=ax, font_size=8)
    ax.set_title(f"r/{subreddit}: who replies to whom (top {len(sub)} users)\n"
                 "size = replies received, colour = community", fontsize=12)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def draw_activity(posts, comments, subreddit, path):
    post_dates = pd.to_datetime(posts["date"])
    comment_dates = pd.to_datetime(comments["date"]) if not comments.empty else pd.Series(dtype="datetime64[ns]")

    fig, (top, bottom) = plt.subplots(2, 1, figsize=(11, 8))
    per_day = pd.concat([post_dates.dt.date.value_counts().rename("Posts"),
                         comment_dates.dt.date.value_counts().rename("Comments")], axis=1).fillna(0).sort_index()
    per_day.index = pd.to_datetime(per_day.index)
    # There are far fewer posts than comments, so posts get their own axis on the right
    per_day.plot(ax=top, marker="o", markersize=3, secondary_y="Posts")
    top.set_title(f"r/{subreddit}: activity per day")
    top.set_ylabel("Comments")
    top.right_ax.set_ylabel("Posts")
    top.set_xlabel("")
    top.grid(alpha=0.3)

    per_hour = pd.concat([post_dates.dt.hour.value_counts().rename("Posts"),
                          comment_dates.dt.hour.value_counts().rename("Comments")], axis=1) \
        .reindex(range(24), fill_value=0).fillna(0)
    (100 * per_hour / per_hour.sum()).plot.bar(ax=bottom, width=0.8)
    bottom.set_title("Share of activity by hour of day (Saudi time, UTC+3)")
    bottom.set_ylabel("%")
    bottom.set_xlabel("Hour")
    bottom.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    # python analyze.py <subreddit> [output_folder]
    if len(sys.argv) < 2:
        sys.exit("Usage: python analyze.py <subreddit> [output_folder]")
    subreddit = sys.argv[1].removeprefix("r/").strip("/")
    folder = find_folder(subreddit)
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(folder, "analysis")

    posts = load_csv(os.path.join(folder, "posts.csv"))
    comments = load_csv(os.path.join(folder, "comments.csv"))
    if posts.empty:
        sys.exit(f"No data for r/{subreddit}. Run sredit.py first.")
    # Only what is shown on the subreddit, even if the data was collected with --all
    if "status" in posts:
        visible = posts["status"] == "visible"
        if not comments.empty:
            comments = comments[comments["post_url"].isin(posts.loc[visible, "url"])]
        posts = posts[visible]

    alias = anonymize(posts, comments)
    g = reply_graph(comments, alias) if not comments.empty else nx.DiGraph()
    stats = user_stats(g)

    os.makedirs(out, exist_ok=True)
    draw_activity(posts, comments, subreddit, os.path.join(out, "activity.png"))
    if not stats.empty:
        stats.to_csv(os.path.join(out, "network_users.csv"), index=False, encoding="utf-8-sig")
        draw_network(g, stats, subreddit, os.path.join(out, "reply_network.png"))

    print(f"Posts: {len(posts)}, comments: {len(comments)}, users: {len(alias)}")
    if not stats.empty:
        print(f"Reply network: {g.number_of_nodes()} users, {g.number_of_edges()} links, "
              f"reciprocity {nx.reciprocity(g):.2f}, {stats['community'].nunique()} communities")
        print("Most replied-to users:")
        print(stats.head(5).to_string(index=False))
    print(f"Saved to: {out}")


if __name__ == "__main__":
    main()
