"""08 Q4: What did TRACKED (loyalty-card) players wager, get back and net, and how concentrated are results?

All player numbers are carded play only (uncarded play is invisible at person level).
Player view: net = payout - coin-in (negative = player lost). Theo = expected house win. Luck = net + theo.
Per-player luck SD from sessions: sqrt(sum over segments of sum_bet_squared * payout_multiplier_variance)
(same exact-variance approach as 03_machine_scorecard). Ratio of sums everywhere.

Writes outputs/08_player_totals.json, 08_lorenz.csv, 08_groups.csv, 08_group_monthly.csv, 08_net_hist.csv."""
from common import con, q, save_csv, save_json
import numpy as np
import pandas as pd

c = con()

# ---------- 1. Totals and reconciliation with machine meters ----------
tot = q(c, """select sum(tracked_coin_in_usd)::double coin_in, sum(tracked_payout_usd)::double payout,
    sum(tracked_net_usd)::double net, sum(theo_usd)::double theo, sum(luck_residual_usd)::double luck,
    sum(linked_pos_cash_usd)::double pos_cash, sum(linked_pos_cogs_usd)::double pos_cogs,
    sum(comp_cost_usd)::double comp_cost, sum(comp_face_value_usd)::double comp_face,
    sum(tracked_contribution_usd)::double contribution from pp""").iloc[0]
mdt = q(c, """select sum(carded_coin_in_cents)/100.0 carded_coin_in, sum(carded_payout_cents)/100.0 carded_payout,
    sum(carded_theo_cents)::double/100.0 carded_theo, sum(coin_in_cents)/100.0 all_coin_in,
    sum(house_win_cents)/100.0 all_house_win, sum(theo_cents)::double/100.0 all_theo from md""").iloc[0]

# ---------- 2. Per-player (6 months) ----------
pl = q(c, """select player_id, any_value(opening_tier) opening_tier, any_value(home_region) home_region,
    coalesce(any_value(age_band),'Unknown') age_band,
    sum(tracked_coin_in_usd)::double coin_in, sum(tracked_payout_usd)::double payout, sum(tracked_net_usd)::double net,
    sum(theo_usd)::double theo, sum(luck_residual_usd)::double luck, sum(observed_visits) visits,
    sum(comp_cost_usd)::double comp_cost, sum(comps_redeemed) comps from pp group by 1""")
lsd = q(c, """with s as (select player_id, config_id, sum(sum_bet_squared) sbb from sessions group by 1,2)
    select player_id, sqrt(sum(sbb * g.payout_multiplier_variance::double))/100.0 luck_sd
    from s join gm g using(config_id) group by 1""")
pl = pl.merge(lsd, on="player_id", how="left")
n_all = len(pl)
g = pl[pl.coin_in > 0].copy()          # players with carded gaming; F&B/loyalty-only players excluded
n = len(g)
g["luck_z"] = g.luck / g.luck_sd

def top_share(v, frac):
    v = np.sort(np.asarray(v, float))[::-1]
    k = max(1, int(round(frac * len(v))))
    return float(v[:k].sum() / v.sum())

def gini(v):
    v = np.sort(np.asarray(v, float)); m = len(v)
    return float((2 * np.arange(1, m + 1) - m - 1).dot(v) / (m * v.sum()))

losers = g[g.net < 0]
loss = -losers.net.values
conc = {}
for name, vals, base in [("coin_in", g.coin_in.values, "all gaming players"),
                         ("theo", g.theo.values, "all gaming players"),
                         ("net_loss_among_losers", loss, "players who ended behind")]:
    conc[name] = {"population": base, "n": int(len(vals)),
                  **{f"top_{int(f*100)}pct_share": top_share(vals, f) for f in (0.01, 0.05, 0.10, 0.20)},
                  "gini": gini(vals)}
# losses of top-1% losers as a share of the ALL-player net (the floor's tracked net loss incl. winners offset)
k1 = max(1, int(round(0.01 * n)))
top1_by_loss = g.nsmallest(k1, "net")
gross_loss = loss.sum(); gross_win = g.loc[g.net > 0, "net"].sum()
conc["top_1pct_of_all_players_by_loss"] = {
    "players": int(k1), "their_net_usd": float(top1_by_loss.net.sum()),
    "share_of_gross_losses": float(-top1_by_loss.net.sum() / gross_loss),
    "share_of_total_tracked_net_loss": float(top1_by_loss.net.sum() / g.net.sum()),
    "their_theo_usd": float(top1_by_loss.theo.sum()), "their_luck_usd": float(top1_by_loss.luck.sum()),
    "their_share_of_coin_in": float(top1_by_loss.coin_in.sum() / g.coin_in.sum()),
    "overlap_with_top_1pct_by_theo": float(top1_by_loss.player_id.isin(g.nlargest(k1, "theo").player_id).mean()),
    "note": "Exposure, not just luck: the biggest losers are mostly the biggest expected-loss (theo) players."}
# Exposure vs luck: how much of the spread in net is explained by theo
r = np.corrcoef(-g.net, g.theo)[0, 1]

ahead = g.net > 0
dist = {"players_with_carded_gaming": n, "players_total_incl_fnb_only": n_all,
        "share_ended_ahead": float(ahead.mean()), "share_exactly_even": float((g.net == 0).mean()),
        "net_percentiles_usd": {f"p{p}": float(np.percentile(g.net, p)) for p in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
        "mean_net_usd": float(g.net.mean()),
        "gross_wins_of_winners_usd": float(gross_win), "gross_losses_of_losers_usd": float(gross_loss),
        "corr_loss_vs_theo": float(r), "r2_loss_vs_theo": float(r * r),
        "share_ahead_by_coin_in_quintile": {}}
g["ci_q"] = pd.qcut(g.coin_in.rank(method="first"), 5, labels=[f"Q{i}" for i in range(1, 6)])
for qn, sub in g.groupby("ci_q", observed=True):
    dist["share_ahead_by_coin_in_quintile"][str(qn)] = {
        "players": int(len(sub)), "coin_in_range_usd": [float(sub.coin_in.min()), float(sub.coin_in.max())],
        "share_ahead": float((sub.net > 0).mean()), "hold": float(-sub.net.sum() / sub.coin_in.sum())}
# luck check: if results are pure chance, |z|>1.96 should be ~5% (normal approx, weak for tiny players)
big = g[g.luck_sd > 0]
dist["luck_check"] = {"players_with_sd": int(len(big)),
                      "share_abs_z_gt_1.96": float((big.luck_z.abs() > 1.96).mean()),
                      "mean_z": float(big.luck_z.mean()), "sd_z": float(big.luck_z.std()),
                      "share_abs_z_gt_1.96_coin_in_ge_10k": float((big[big.coin_in >= 10000].luck_z.abs() > 1.96).mean()),
                      "note": "Per-player luck z = (net + theo)/SD; SD from sum_bet_squared x game payout variance. "
                              "Near 5% / SD~1 means winners and losers beyond theo are consistent with chance."}

# ---------- 3. Lorenz curves (~100 points, ascending) ----------
pts = np.linspace(0, 1, 101)
lor = {"pop_share": pts}
for name, vals in [("coin_in", g.coin_in.values), ("theo", g.theo.values), ("net_loss_among_losers", loss)]:
    v = np.sort(vals); cs = np.concatenate([[0], np.cumsum(v)]) / v.sum()
    idx = np.round(pts * len(v)).astype(int)
    lor[f"cum_share_{name}"] = cs[idx]
save_csv("08_lorenz.csv", pd.DataFrame(lor).round(6))

# ---------- 4. Net histogram (binned) ----------
edges = [-np.inf, -50000, -20000, -10000, -5000, -2000, -1000, -500, -200, -100, -50, -20, 0, 0.005,
         20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, np.inf]
labels = []
for a, b in zip(edges[:-1], edges[1:]):
    if a == 0 and b == 0.005: labels.append("0 (even)")
    elif a == 0.005: labels.append(f"0 to {b:g}")
    elif np.isinf(a): labels.append(f"< {b:g}")
    elif np.isinf(b): labels.append(f">= {a:g}")
    else: labels.append(f"{a:g} to {b:g}")
g["bin"] = pd.cut(g.net, edges, right=False, labels=labels)
h = g.groupby("bin", observed=False).agg(players=("net", "size"), net_usd=("net", "sum"),
                                          coin_in_usd=("coin_in", "sum"), theo_usd=("theo", "sum")).reset_index()
h.insert(1, "lo", edges[:-1]); h.insert(2, "hi", edges[1:])
h["share_players"] = h.players / n
save_csv("08_net_hist.csv", h.replace([np.inf, -np.inf], np.nan).round(4))

# ---------- 5. Guest groups ----------
pl["ahead"] = (pl.net > 0).astype(int); pl["gaming"] = (pl.coin_in > 0).astype(int)
rows = []
for gt in ["opening_tier", "home_region", "age_band"]:
    agg = pl.groupby(gt).agg(players=("player_id", "size"), gaming_players=("gaming", "sum"),
                             coin_in=("coin_in", "sum"), payout=("payout", "sum"), net=("net", "sum"),
                             theo=("theo", "sum"), luck=("luck", "sum"), visits=("visits", "sum"),
                             comps=("comps", "sum"), comp_cost=("comp_cost", "sum"),
                             players_ahead=("ahead", "sum")).reset_index().rename(columns={gt: "group"})
    agg.insert(0, "group_type", gt); rows.append(agg)
allrow = pl.assign(group="All").groupby("group").agg(players=("player_id", "size"), gaming_players=("gaming", "sum"),
        coin_in=("coin_in", "sum"), payout=("payout", "sum"), net=("net", "sum"), theo=("theo", "sum"),
        luck=("luck", "sum"), visits=("visits", "sum"), comps=("comps", "sum"), comp_cost=("comp_cost", "sum"),
        players_ahead=("ahead", "sum")).reset_index()
allrow.insert(0, "group_type", "all"); rows.insert(0, allrow)
grp = pd.concat(rows, ignore_index=True)

def derive(d):
    d["hold"] = -d.net / d.coin_in
    d["theo_hold"] = d.theo / d.coin_in
    d["net_per_gaming_player"] = d.net / d.gaming_players
    d["theo_per_gaming_player"] = d.theo / d.gaming_players
    d["coin_in_per_gaming_player"] = d.coin_in / d.gaming_players
    d["share_ahead"] = d.players_ahead / d.gaming_players
    d["comp_cost_per_theo"] = d.comp_cost / d.theo
    d["share_of_tracked_coin_in"] = d.coin_in / tot.coin_in
    d["share_of_tracked_theo"] = d.theo / tot.theo
    return d
grp = derive(grp)
save_csv("08_groups.csv", grp.round(6))

# monthly (a player's monthly 'ahead' = that month's net > 0)
mon = []
for gt in ["opening_tier", "home_region", "age_band"]:
    m = q(c, f"""select '{gt}' group_type, coalesce({gt},'Unknown') "group", period_month "month",
        count(*) players, count(*) filter (where tracked_coin_in_usd>0) gaming_players,
        sum(tracked_coin_in_usd)::double coin_in, sum(tracked_payout_usd)::double payout, sum(tracked_net_usd)::double net,
        sum(theo_usd)::double theo, sum(luck_residual_usd)::double luck, sum(observed_visits) visits,
        sum(comps_redeemed) comps, sum(comp_cost_usd)::double comp_cost,
        count(*) filter (where tracked_net_usd>0) players_ahead,
        sum(points_earned) points_earned, sum(points_redeemed) points_redeemed
        from pp group by all order by 2,3""")
    mon.append(m)
mon = derive(pd.concat(mon, ignore_index=True))
save_csv("08_group_monthly.csv", mon.round(6))

# ---------- 6. JSON ----------
tier = grp[grp.group_type == "opening_tier"].set_index("group")
res = {
    "scope": "TRACKED (loyalty-card) play only, gaming dates 2025-01-01..2025-06-30. Player view: net = payout - coin-in.",
    "totals_usd": {"tracked_coin_in": float(tot.coin_in), "tracked_payout": float(tot.payout),
                   "tracked_net_player_view": float(tot.net), "theo": float(tot.theo),
                   "luck_residual_player_view": float(tot.luck),
                   "tracked_hold": float(-tot.net / tot.coin_in), "theo_hold": float(tot.theo / tot.coin_in),
                   "linked_pos_cash": float(tot.pos_cash), "linked_pos_cogs_incl_comps": float(tot.pos_cogs),
                   "comp_economic_cost": float(tot.comp_cost), "comp_face_value_not_revenue": float(tot.comp_face),
                   "tracked_contribution": float(tot.contribution)},
    "reconciliation_vs_machine_day_carded": {
        "md_carded_coin_in": float(mdt.carded_coin_in), "md_carded_payout": float(mdt.carded_payout),
        "md_carded_theo": float(mdt.carded_theo),
        "diff_coin_in": float(tot.coin_in - mdt.carded_coin_in), "diff_payout": float(tot.payout - mdt.carded_payout),
        "diff_theo": float(tot.theo - mdt.carded_theo),
        "carded_share_of_all_coin_in": float(mdt.carded_coin_in / mdt.all_coin_in),
        "carded_share_of_all_theo": float(mdt.carded_theo / mdt.all_theo),
        "uncarded_house_win": float(mdt.all_house_win - (mdt.carded_coin_in - mdt.carded_payout))},
    "distribution": dist,
    "concentration": conc,
    "tier_summary": tier[["players", "gaming_players", "share_of_tracked_coin_in", "share_of_tracked_theo",
                          "net_per_gaming_player", "share_ahead", "comp_cost_per_theo"]].to_dict(orient="index"),
    "limitations": ["Uncarded play (~34% of coin-in) is invisible per person; a carded guest may also play uncarded.",
                    "Six-month window: 'ahead' is over this window only; short-tenure players have few spins so "
                    "their results are mostly luck.",
                    "Opening tier is fixed at 2025-01-01; tier changes within the window are not reflected in groups."],
}
save_json("08_player_totals.json", res)
print({k: round(v, 2) for k, v in res["totals_usd"].items()})
print(res["reconciliation_vs_machine_day_carded"])
print({k: v for k, v in dist.items() if k != "share_ahead_by_coin_in_quintile"})
print(dist["share_ahead_by_coin_in_quintile"])
print(conc)
print(grp[["group_type", "group", "players", "gaming_players", "coin_in", "net", "theo", "net_per_gaming_player",
           "share_ahead", "comp_cost", "comp_cost_per_theo"]].round(4).to_string())
