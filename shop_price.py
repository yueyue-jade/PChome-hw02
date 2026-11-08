# -*- coding: utf-8 -*-
"""
PChome + momo 商品價格比較與視覺化（Streamlit）
執行方式：
    pip install -r requirements.txt
    streamlit run shop_price.py
"""
import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
PCHOME_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
MOMO_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# momo 的 header 盡量像一般瀏覽器（UA 沿用老師程式碼），降低被 403 擋下的機會
MOMO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}
REQUEST_DELAY = 1.5  # 每抓一頁後休息幾秒，避免被網站封鎖
PLATFORMS = ["PChome", "momo"]
PLATFORM_COLORS = {"PChome": "#6366F1", "momo": "#F43F5E"}
# 價格區間用「由淺到深」的顏色，越深代表越貴（中文由瀏覽器顯示，不需安裝字型）
BAND_COLORS = ["#C7D2FE", "#A5B4FC", "#818CF8", "#6366F1", "#4338CA", "#312E81", "#1E1B4B"]
CHART_FONT = "Noto Sans TC, Microsoft JhengHei, PingFang TC, sans-serif"

STORE_WORDS = ("購物", "商城", "旗艦", "官方", "專賣", "商店", "館")
PROMO_WORDS = ("特價", "免運", "限時", "加碼", "現貨", "熱銷", "下殺", "優惠", "送", "贈", "折")
NOT_BRAND = {"USB", "LED", "PC", "DC", "AC", "RGB", "LCD", "TYPE"}
BAND_STEPS = [50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000,
              100000, 200000, 500000]


# ----------------------------------------------------------------------
# 資料：爬取（兩個平台都整理成同一種格式：id / name / price / text）
# ----------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_pchome(keyword: str, page: int) -> list:
    """PChome：抓單一頁。"""
    resp = requests.get(
        PCHOME_URL,
        params={"q": keyword, "page": page, "sort": "sale/dc"},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    prods = resp.json().get("prods") or []
    time.sleep(REQUEST_DELAY)
    return [
        {
            "id": p.get("Id") or p.get("name"),
            "name": p.get("name"),
            "price": p.get("price"),
            "text": p.get("describe") or p.get("name"),  # 用來猜品牌
        }
        for p in prods
    ]


def build_momo_payload(keyword: str, page: int) -> dict:
    """momo 搜尋用的 payload（欄位沿用原始 CODE，關鍵字與頁碼改成可變）。"""
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "specialGoodsType": "", "isBrandSeriesPage": "false", "authorNo": "",
            "originalCateCode": "", "cateType": "", "searchValue": keyword,
            "cateCode": "", "cateLevel": "-1", "cp": "N", "NAM": "N", "first": "N",
            "freeze": "N", "superstore": "N", "tvshop": "N", "china": "N",
            "tomorrow": "N", "stockYN": "N", "prefere": "N", "threeHours": "N",
            "video": "N", "cycle": "N", "cod": "N", "superstorePay": "N",
            "showType": "chessboardType", "curPage": str(page),
            "priceS": "0", "priceE": "9999999", "searchType": "1",
            "reduceKeyword": "", "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "", "cateLv": "-1", "keyword": keyword,
                "curPage": str(page), "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018, "serviceCode": "MT01", "addressSearchData": {},
            "adSource": "tenmax",
        },
    }


@st.cache_resource(show_spinner=False)
def get_momo_session() -> requests.Session:
    """建立 momo 專用 Session：先逛一次首頁拿 cookie，比較像一般瀏覽器。"""
    s = requests.Session()
    s.headers.update(MOMO_HEADERS)
    try:
        s.get("https://www.momoshop.com.tw/", timeout=10)
    except requests.RequestException:
        pass  # 首頁連不上也沒關係，後面的搜尋請求會自己報錯
    return s


@st.cache_data(ttl=600, show_spinner=False)
def fetch_momo(keyword: str, page: int) -> list:
    """momo：抓單一頁。"""
    resp = get_momo_session().post(
        MOMO_URL, json=build_momo_payload(keyword, page), timeout=15
    )
    resp.raise_for_status()
    goods = (resp.json().get("rtnSearchData") or {}).get("goodsInfoList") or []
    time.sleep(REQUEST_DELAY)
    return [
        {
            "id": g.get("goodsCode") or g.get("goodsName"),
            "name": g.get("goodsName"),
            "price": g.get("goodsPrice"),
            "text": g.get("goodsName"),
        }
        for g in goods
    ]


FETCHERS = {"PChome": fetch_pchome, "momo": fetch_momo}


def read_momo_csv(file) -> list:
    """讀取 momo CSV（老師程式產生的 MOMO_*.csv，欄位是「品名」「價格」），轉成同一種格式。"""
    data = pd.read_csv(file)
    name_col = next((c for c in ("品名", "商品名稱", "name") if c in data.columns), None)
    price_col = next((c for c in ("價格", "price") if c in data.columns), None)
    if name_col is None or price_col is None:
        raise ValueError("CSV 需要有「品名」和「價格」兩個欄位")
    return [
        {"id": f"csv{i}", "name": str(n), "price": p, "text": str(n), "平台": "momo"}
        for i, (n, p) in enumerate(zip(data[name_col], data[price_col]))
    ]


def collect(keyword: str, platforms: list, pages: int):
    """依序抓取各平台多頁。回傳 (資料列, {平台: 錯誤訊息})。某平台失敗不影響另一個。"""
    rows, errors = [], {}
    total = len(platforms) * pages
    done = 0
    bar = st.progress(0.0, text="準備查詢…")
    try:
        for plat in platforms:
            for page in range(1, pages + 1):
                bar.progress(done / total, text=f"{plat}：正在抓取第 {page} / {pages} 頁…")
                try:
                    items = FETCHERS[plat](keyword, page)
                except (requests.RequestException, ValueError) as e:
                    errors[plat] = str(e)
                    break  # 這個平台放棄，換下一個
                done += 1
                if not items:  # 這一頁沒資料，後面也不會有
                    done += pages - page
                    break
                rows += [{**it, "平台": plat} for it in items]
        bar.progress(1.0, text="抓取完成")
    finally:
        bar.empty()
    return rows, errors


# ----------------------------------------------------------------------
# 資料：清理
# ----------------------------------------------------------------------
def parse_price(value):
    """價格字串只取第一組數字：'$1,099' → 1099，'$1,099~$1,299' → 1099。"""
    if isinstance(value, (int, float)):
        return value
    m = re.search(r"\d[\d,]*", str(value or ""))
    return int(m.group(0).replace(",", "")) if m else None


def extract_brand(text) -> str:
    """從文字猜品牌：優先【品牌】，其次開頭的英文字，抓不到就是「其他」。"""
    if not isinstance(text, str) or not text.strip():
        return "其他"
    first = re.split(r"\\r\\n|\r?\n", text.strip())[0]

    tags = list(re.finditer(r"【(.+?)】", first))
    for m in tags:
        t = m.group(1).strip()
        if len(t) <= 12 and not any(w in t for w in STORE_WORDS + PROMO_WORDS):
            return t.upper() if t.isascii() else t

    rest = first[tags[-1].end():].strip() if tags else first.strip()
    m = re.match(r"[A-Za-z][A-Za-z0-9\-]+", rest)
    if m and m.group(0).upper() not in NOT_BRAND:
        return m.group(0).upper()
    return "其他"


def add_price_band(df: pd.DataFrame) -> pd.DataFrame:
    """把價格切成整數級距（例如 $0–$999、$1,000–$1,999），所有平台共用同一組級距。"""
    p = df["價格"]
    top = float(p.quantile(0.95))  # 用第 95 百分位決定上限，避免極端高價把級距撐爆
    span = max(top - float(p.min()), 1.0)
    step = next((s for s in BAND_STEPS if s >= span / 5), BAND_STEPS[-1])
    lo = int(p.min() // step * step)
    hi = int(-(-top // step) * step)
    if hi <= lo:
        hi = lo + step
    edges = list(range(lo, hi + 1, step))
    labels = [f"${a:,}–${b - 1:,}" for a, b in zip(edges[:-1], edges[1:])]
    labels.append(f"${hi:,} 以上")
    df["價格區間"] = pd.cut(p, bins=edges + [float("inf")], labels=labels, right=False)
    return df


def prepare(rows: list) -> pd.DataFrame:
    """把原始資料整理成分析用的表格。"""
    df = pd.DataFrame(rows).reindex(columns=["平台", "id", "name", "price", "text"])
    df["價格"] = pd.to_numeric(df["price"].map(parse_price), errors="coerce")
    df = df.dropna(subset=["價格", "name"])
    df = df[df["價格"] > 0].drop_duplicates(subset=["平台", "id"]).reset_index(drop=True)
    df["價格"] = df["價格"].astype(int)
    df["排序"] = df.groupby("平台").cumcount() + 1
    df["品牌"] = df["text"].map(extract_brand)
    df = df.rename(columns={"name": "商品名稱"})[["平台", "排序", "商品名稱", "品牌", "價格"]]
    return add_price_band(df)


# ----------------------------------------------------------------------
# 重點結論（全部由資料計算，不是寫死的文字）
# ----------------------------------------------------------------------
def money(x) -> str:
    return f"${x:,.0f}"


def band_counts(df: pd.DataFrame) -> pd.Series:
    """各價格區間的商品數（依價格由低到高，沒有商品的區間不列出）。"""
    c = df.groupby("價格區間", observed=True).size()
    return c[c > 0]


def brand_stats(df: pd.DataFrame) -> pd.DataFrame:
    """出現 2 件以上、而且判斷得出品牌的品牌統計。"""
    b = df[df["品牌"] != "其他"].groupby("品牌")["價格"].agg(件數="size", 中位價="median")
    return b[b["件數"] >= 2].reset_index()


def build_insights(df: pd.DataFrame) -> dict:
    n = len(df)
    plats = list(df["平台"].unique())
    med, avg = df["價格"].median(), df["價格"].mean()
    q1, q3 = df["價格"].quantile([0.25, 0.75])
    pmin, pmax = df["價格"].min(), df["價格"].max()
    summary = []

    # 1) 價格概況
    if avg > med * 1.1:
        shape = "平均高於中位數，代表少數高價商品把平均拉高，抓預算時看中位價比較準"
    elif avg < med * 0.9:
        shape = "平均低於中位數，代表有一批低價商品把平均拉低"
    else:
        shape = "平均與中位數很接近，價格分布算平均"
    summary.append(
        f"**價格概況**：中位價 {money(med)}、平均 {money(avg)}（{shape}）；"
        f"最便宜 {money(pmin)}，最貴 {money(pmax)}，相差約 {pmax / pmin:.1f} 倍。"
    )

    # 2) 主流價位
    bc = band_counts(df)
    top_band = bc.idxmax()
    summary.append(
        f"**主流價位**：中間五成的商品落在 {money(q1)} ~ {money(q3)}；"
        f"最多商品集中在 {top_band}（佔 {bc.max() / n:.0%}）。"
    )

    # 3) 平台比較
    parts = []
    for pl in plats:
        vc = df[df["平台"] == pl]["價格區間"].astype(str).value_counts(normalize=True)
        parts.append(f"{pl} 有 {vc.iloc[0]:.0%} 的商品落在 {vc.index[0]}")
    pie_text = "；".join(parts)
    if len(plats) >= 2:
        pm = df.groupby("平台")["價格"].median().sort_values()
        cheap, pricey = pm.index[0], pm.index[-1]
        diff = (pm.iloc[-1] - pm.iloc[0]) / pm.iloc[-1]
        if diff < 0.05:
            summary.append(
                f"**平台比較**：{cheap} 與 {pricey} 的中位價只差約 {diff:.0%}，整體價位相近。"
            )
        else:
            summary.append(
                f"**平台比較**：{cheap} 的中位價（{money(pm.iloc[0])}）比 {pricey}"
                f"（{money(pm.iloc[-1])}）低約 {diff:.0%}。"
                "（比的是整體價位，不保證是同款商品。）"
            )
        below = df.assign(便宜=df["價格"] < med).groupby("平台")["便宜"].mean()
        pie_text += "。低於整體中位價的商品比例：" + "、".join(
            f"{pl} {below[pl]:.0%}" for pl in plats
        )

    # 4) 品牌觀察
    bs = brand_stats(df)
    sun_text = ""
    if not bs.empty:
        most = bs.sort_values(["件數", "中位價"], ascending=[False, True]).iloc[0]
        cheapest = bs.sort_values("中位價").iloc[0]
        priciest = bs.sort_values("中位價").iloc[-1]
        sun_text = (
            f"商品最多的品牌是 {most['品牌']}（{int(most['件數'])} 件，中位價 {money(most['中位價'])}）"
        )
        line = f"**品牌觀察**：{sun_text}"
        if cheapest["品牌"] != priciest["品牌"]:
            line += (
                f"；中位價最低的是 {cheapest['品牌']}（{money(cheapest['中位價'])}），"
                f"最高的是 {priciest['品牌']}（{money(priciest['中位價'])}）"
            )
            sun_text += f"；中位價最高的品牌是 {priciest['品牌']}"
        summary.append(line + "。（只統計出現 2 件以上、且判斷得出品牌的商品。）")
    big = df.groupby(["平台", df["價格區間"].astype(str)]).size().idxmax()
    sun_text = (f"最大的一塊是「{big[0]} → {big[1]}」。" + sun_text).rstrip("；")

    # 5) 排名與價格
    line_text = ""
    if n >= 10:
        pos = df["排序"] / df.groupby("平台")["排序"].transform("max")
        r = pos.corr(df["價格"].rank(pct=True))
        if pd.notna(r):
            if r <= -0.2:
                rel = "搜尋結果排越前面的商品，價格傾向越高"
            elif r >= 0.2:
                rel = "搜尋結果排越前面的商品，價格傾向越低"
            else:
                rel = "排名和價格幾乎沒有關係，前面的商品不一定比較貴或比較便宜"
            line_text = rel + "。"
            summary.append(f"**排名與價格**：{rel}。")
    means = "、".join(f"{pl} 平均 {money(df[df['平台'] == pl]['價格'].mean())}" for pl in plats)
    line_text += f"虛線是各平台平均價：{means}。"

    if n < 20:
        summary.append(f"**提醒**：目前只有 {n} 件資料，樣本偏少，結論僅供參考。")
    return {"summary": summary, "line": line_text, "pie": pie_text + "。", "sunburst": sun_text + "。"}


# ----------------------------------------------------------------------
# 圖表（Plotly）
# ----------------------------------------------------------------------
def _style(fig: go.Figure, title: str, height: int = 540) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=20)),
        font=dict(family=CHART_FONT, size=14),
        margin=dict(l=20, r=20, t=80, b=40),
        height=height,
    )
    return fig


def line_chart(df: pd.DataFrame, keyword: str) -> go.Figure:
    fig = go.Figure()
    for pl in df["平台"].unique():
        sub = df[df["平台"] == pl]
        color = PLATFORM_COLORS.get(pl, "#10B981")
        fig.add_trace(
            go.Scatter(
                x=sub["排序"], y=sub["價格"], name=pl, mode="lines+markers",
                line=dict(color=color, width=3), marker=dict(size=7),
                customdata=sub["商品名稱"].str.slice(0, 30),
                hovertemplate=pl + "<br>排序 %{x}<br>價格 $%{y:,}<br>%{customdata}<extra></extra>",
            )
        )
        mean = sub["價格"].mean()
        fig.add_hline(
            y=mean, line=dict(color=color, dash="dash", width=1.5),
            annotation_text=f"{pl} 平均 ${mean:,.0f}", annotation_font_color=color,
            annotation_position="top right",
        )
    fig.update_xaxes(title="排序（各平台搜尋結果的出現順序）")
    fig.update_yaxes(title="價格 (NT$)")
    fig.update_layout(legend=dict(orientation="h", y=1.08, x=0))
    return _style(fig, f"「{keyword}」售價走勢")


def pie_chart(df: pd.DataFrame, keyword: str) -> go.Figure:
    bands = [str(b) for b in band_counts(df).index]  # 所有平台共用的區間與顏色
    color_of = {b: BAND_COLORS[min(i, len(BAND_COLORS) - 1)] for i, b in enumerate(bands)}
    plats = list(df["平台"].unique())
    k, gap = len(plats), 0.10
    width = (1 - gap * (k - 1)) / k
    fig = go.Figure()
    for i, pl in enumerate(plats):
        x0 = i * (width + gap)
        cnt = df[df["平台"] == pl]["價格區間"].astype(str).value_counts()
        cnt = cnt.reindex([b for b in bands if b in cnt.index])
        fig.add_trace(
            go.Pie(
                labels=cnt.index, values=cnt.values, hole=0.5, sort=False,
                domain=dict(x=[x0, x0 + width], y=[0, 0.9]),
                marker=dict(colors=[color_of[b] for b in cnt.index],
                            line=dict(color="white", width=2)),
                textinfo="label+percent", textposition="outside",
                hovertemplate="%{label}<br>%{value} 件 (%{percent})<extra></extra>",
                showlegend=False,
            )
        )
        cx = x0 + width / 2
        fig.add_annotation(x=cx, y=0.45, text=f"<b>{int(cnt.sum())}</b><br>件",
                           showarrow=False, font=dict(size=20))
        fig.add_annotation(x=cx, y=1.0, text=f"<b>{pl}</b>", showarrow=False,
                           font=dict(size=18, color=PLATFORM_COLORS.get(pl, "#10B981")))
    return _style(fig, f"「{keyword}」價格區間佔比（顏色越深越貴）", height=520)


def sunburst_chart(df: pd.DataFrame, keyword: str) -> go.Figure:
    data = df.assign(商品數=1, 價格區間=df["價格區間"].astype(str))
    fig = px.sunburst(
        data, path=["平台", "價格區間", "品牌"], values="商品數",
        color="價格", color_continuous_scale="Viridis",
    )
    fig.update_traces(
        hovertemplate="<b>%{label}</b><br>商品數 %{value}<br>平均價 $%{color:,.0f}<extra></extra>",
        insidetextorientation="radial",
    )
    fig.update_coloraxes(colorbar_title="平均價<br>NT$")
    return _style(fig, f"「{keyword}」平台 → 價格區間 → 品牌（顏色＝平均價）", height=620)


# ----------------------------------------------------------------------
# 頁面外觀
# ----------------------------------------------------------------------
CSS = """
<style>
.block-container {padding-top: 2rem;}
.hero {
    background: linear-gradient(120deg, #6366F1 0%, #F43F5E 100%);
    border-radius: 18px; padding: 28px 34px; color: #fff; margin-bottom: 22px;
    box-shadow: 0 8px 24px rgba(99,102,241,.25);
}
.hero h1 {margin: 0; font-size: 2rem; color: #fff;}
.hero p {margin: 6px 0 0; opacity: .92; font-size: 1.02rem;}
.card {
    background: rgba(128,128,128,.10); border-radius: 14px; padding: 16px 18px;
    border-left: 6px solid var(--accent); height: 100%;
}
.card .label {font-size: .85rem; opacity: .7;}
.card .value {font-size: 1.8rem; font-weight: 700; line-height: 1.3;}
.card .sub {font-size: .78rem; opacity: .65; overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap;}
[data-testid="stSidebar"] .stButton > button {width: 100%; font-weight: 600;}
</style>
"""


def card(label: str, value: str, sub: str, accent: str) -> str:
    sub = sub.replace("<", "＜").replace(">", "＞")
    return (
        f'<div class="card" style="--accent:{accent}">'
        f'<div class="label">{label}</div><div class="value">{value}</div>'
        f'<div class="sub">{sub}</div></div>'
    )


def show_cards(df: pd.DataFrame) -> None:
    top = df.loc[df["價格"].idxmax()]
    low = df.loc[df["價格"].idxmin()]
    items = [
        ("商品數量", f"{len(df)} 件", "、".join(df["平台"].unique()), "#6366F1"),
        ("中位價格", money(df["價格"].median()), f"平均 {money(df['價格'].mean())}", "#06B6D4"),
        ("最低價格", money(low["價格"]), f"{low['平台']}｜{low['商品名稱']}", "#10B981"),
        ("最高價格", money(top["價格"]), f"{top['平台']}｜{top['商品名稱']}", "#F43F5E"),
    ]
    for col, it in zip(st.columns(4), items):
        col.markdown(card(*it), unsafe_allow_html=True)


def platform_table(df: pd.DataFrame) -> pd.DataFrame:
    t = df.groupby("平台")["價格"].agg(
        商品數="size", 平均價="mean", 中位價="median", 最低價="min", 最高價="max"
    )
    return t.round(0).astype(int).reset_index()


def show_dashboard(keyword: str, df: pd.DataFrame, errors: dict) -> None:
    for plat, msg in errors.items():
        hint = "可以在左側「上傳 momo CSV」改用檔案。" if plat == "momo" else ""
        st.warning(f"{plat} 連線失敗，目前沒有它的資料。{hint}（{msg}）")

    ins = build_insights(df)
    show_cards(df)
    st.write("")
    with st.container(border=True):
        st.subheader("重點結論")
        for s in ins["summary"]:
            st.markdown(f"- {s}")
        st.dataframe(platform_table(df), hide_index=True)

    tab1, tab2, tab3, tab4 = st.tabs(["折線圖", "圓餅圖", "旭日圖", "資料表"])
    with tab1:
        st.plotly_chart(line_chart(df, keyword))
        st.info(ins["line"])
    with tab2:
        st.plotly_chart(pie_chart(df, keyword))
        st.info(ins["pie"])
    with tab3:
        st.plotly_chart(sunburst_chart(df, keyword))
        st.info(ins["sunburst"])
        st.caption("品牌是從商品名稱或描述自動判斷的，抓不到的歸在「其他」，僅供參考。")
    with tab4:
        shown = df.assign(價格區間=df["價格區間"].astype(str))
        st.dataframe(shown, hide_index=True)
        st.download_button(
            "下載 CSV",
            shown.to_csv(index=False).encode("utf-8-sig"),  # utf-8-sig：Excel 開啟中文不會亂碼
            file_name=f"price_{keyword}.csv",
            mime="text/csv",
        )


# ----------------------------------------------------------------------
# 主程式
# ----------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="商品價格比較", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>PChome x momo 商品價格比較</h1>'
        "<p>輸入商品、選擇平台與頁數，看價格走勢、區間佔比、品牌分布和自動整理的結論</p></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("查詢條件")
        keyword = st.text_input("商品關鍵字", value="螢幕掛燈")
        platforms = st.multiselect("平台", PLATFORMS, default=PLATFORMS)
        pages = st.slider("爬取頁數（每頁約 20 件）", min_value=1, max_value=10, value=2)
        momo_file = st.file_uploader(
            "上傳 momo CSV（選填）", type="csv",
            help="momo 連不上時，可上傳老師程式產生的 MOMO_*.csv（欄位：品名、價格），"
                 "上傳後 momo 就不再連網，直接用檔案內容。",
        )
        search = st.button("開始查詢", type="primary")
        st.caption(f"每頁間隔 {REQUEST_DELAY} 秒，避免被網站封鎖。")

    if search:
        kw = keyword.strip()
        if not kw:
            st.warning("請先輸入商品關鍵字。")
        elif not platforms:
            st.warning("請至少選擇一個平台。")
        else:
            use_file = momo_file is not None and "momo" in platforms
            online = [p for p in platforms if not (p == "momo" and use_file)]
            rows, errors = collect(kw, online, pages) if online else ([], {})
            if use_file:
                try:
                    rows += read_momo_csv(momo_file)
                except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as e:
                    errors["momo"] = f"CSV 讀取失敗：{e}"
            df = prepare(rows) if rows else pd.DataFrame()
            if df.empty:
                st.session_state.pop("result", None)
                if errors:
                    for plat, msg in errors.items():
                        st.error(f"{plat} 連線或資料解析失敗，請稍後再試。（{msg}）")
                else:
                    st.warning(f"找不到「{kw}」的商品，換個關鍵字試試。")
            else:
                st.session_state["result"] = (kw, df, errors)

    result = st.session_state.get("result")
    if result is None:
        st.info("在左側輸入商品關鍵字、選擇平台和頁數，按「開始查詢」。")
        return
    show_dashboard(*result)


if __name__ == "__main__":
    main()
