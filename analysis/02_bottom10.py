"""02 Reproduce the manager's bottom-10 list exactly as in GUIDE.md. Writes outputs/02_bottom10.csv."""
from common import con, q, save_csv, RAW

c = con()
df = q(c, f"""
    SELECT machine_id,
           SUM(house_win_usd)                    AS house_win,
           SUM(theo_usd)                         AS expected_win,
           SUM(house_win_usd) / NULLIF(SUM(coin_in_usd), 0) AS observed_hold,
           SUM(available_hours) /
               (SUM(c.scheduled_seconds) / 3600.0) AS availability
    FROM '{RAW}/1_prepared_views/prepared_daily_view.parquet' AS d
    JOIN '{RAW}/2_machine_decision/calendar_day.parquet' AS c
      USING (gaming_date)
    WHERE d.active
    GROUP BY machine_id
    ORDER BY house_win
    LIMIT 10""")
df.insert(0, "manager_rank", range(1, 11))
save_csv("02_bottom10.csv", df)
print(df.round(3).to_string(index=False))
