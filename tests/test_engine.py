from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from radio import db, digest, process
from radio.collectors import feeds, liquipedia
from radio.entities import Entity, Matcher, save_entity
from radio.models import KIND_COMMUNITY, KIND_DATA, KIND_PRESS, Item
from radio.pipeline import rank
from radio.pipeline.cluster import Cluster, assign

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def item(title, source="google-news", kind=KIND_PRESS, author="Marca", hours_ago=1, url=None):
    return Item(
        source=source,
        kind=kind,
        url=url or f"https://example.com/{abs(hash((title, author)))}",
        title=title,
        author=author,
        published_at=NOW - timedelta(hours=hours_ago),
    )


def test_matcher_ambiguous_alias_needs_exact_case():
    m = Matcher([Entity(name="Locked", country="ES", ambiguous=True), Entity(name="Zyrex", country="ES")])
    assert [e.name for e in m.find("the chest was locked")] == []
    assert [e.name for e in m.find("Locked se clasifica para la final")] == ["Locked"]
    assert [e.name for e in m.find("ZYREX ficha por un equipo")] == ["Zyrex"]


def test_matcher_finds_by_handle_and_author():
    e = Entity(name="Fortnite Competitive", x="FNCompetitive")
    m = Matcher([e])
    assert m.find("según @FNCompetitive hay cambios")
    assert m.find("texto sin nombre", author="@FNCompetitive")


def test_same_news_from_two_outlets_joins_one_story():
    clusters: list[Cluster] = []
    a = assign(item("Zyrex ficha por Team Heretics para la FNCS", author="Marca"), {"p:zyrex"}, clusters)
    b = assign(item("Team Heretics anuncia el fichaje de Zyrex", author="AS"), {"p:zyrex"}, clusters)
    c = assign(item("Epic publica el parche 38.10 con cambios competitivos", author="Dexerto"), set(), clusters)
    assert a is b
    assert c is not a
    assert len(clusters) == 2


def test_levels():
    official = {"fncompetitive"}
    assert rank.level([item("x", kind=KIND_DATA)], official) == rank.OFICIAL
    tweet = item("Nuevas reglas", source="x", kind="social", author="FNCompetitive")
    assert rank.level([tweet], official) == rank.OFICIAL
    two = [item("Zyrex ficha", author="Marca"), item("Zyrex ficha por X", author="AS")]
    assert rank.level(two, official) == rank.CONFIRMADO
    assert rank.level([item("Rumor: Zyrex ficha", kind=KIND_COMMUNITY, author="u1")], official) == rank.RUMOR


def test_spain_scores_above_same_news_elsewhere():
    items = [item("X ficha por un equipo")]
    t = rank.tags(items[0].body)
    es = rank.score(items, rank.CONFIRMADO, "ES", t, NOW)
    na = rank.score(items, rank.CONFIRMADO, "NAC", t, NOW)
    assert es > na


def test_sections():
    assert rank.section(rank.RUMOR, "ES", {"fichaje"}) == "rumores"
    assert rank.section(rank.OFICIAL, "ES", {"parche"}) == "espana"
    assert rank.section(rank.OFICIAL, "", {"parche"}) == "juego"
    assert rank.section(rank.OFICIAL, "NAC", {"victoria"}) == "torneos"


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Zyrex gana la Cash Cup</title><link>https://n.example/1</link>
<pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate><source url="https://marca.com">Marca</source>
<description>&lt;b&gt;Gran&lt;/b&gt; partida</description></item></channel></rss>"""


def test_parse_feed_uses_outlet_as_author():
    [it] = feeds.parse_feed(RSS, "google-news", KIND_PRESS, "es")
    assert it.author == "Marca"
    assert it.text == "Gran partida"
    assert it.published_at == datetime(2026, 10, 26, 10, tzinfo=timezone.utc)


WIKITEXT = """{{Infobox player
|id=Zyrex
|name=[[Ejemplo|Nombre Real]]
|country=Spain
|location=Madrid
|team=Team Heretics
|twitter=ZyrexFN
}}
Zyrex es un jugador..."""


def test_liquipedia_infobox_and_team_change():
    fields = liquipedia.parse_infobox(WIKITEXT)
    assert fields["team"] == "Team Heretics"
    assert fields["name"] == "Nombre Real"
    e = liquipedia.entity_from_page("Zyrex", "player", fields)
    assert (e.country, e.city, e.x) == ("ES", "Madrid", "ZyrexFN")

    class FakeClient:
        team = "Team Heretics"

        def category(self, title):
            return ["Zyrex"] if "Players" in title else []

        def wikitext(self, titles):
            return {t: WIKITEXT.replace("Team Heretics", self.team) for t in titles}

    conn = db.connect(":memory:")
    fake = FakeClient()
    assert liquipedia.sync(conn, fake) == []  # primera vez: solo foto
    fake.team = "Team Queso"
    [change] = liquipedia.sync(conn, fake)
    assert change.title == "Zyrex deja Team Heretics y ahora figura en Team Queso"


def test_full_pipeline_and_digest():
    conn = db.connect(":memory:")
    save_entity(conn, Entity(name="Zyrex", country="ES", region="EU"))
    db.save_items(
        conn,
        [
            item("Zyrex ficha por Team Heretics", author="Marca"),
            item("Team Heretics anuncia el fichaje de Zyrex", author="AS"),
            item("Peterbot gana la FNCS de NA", author="Dexerto", hours_ago=2),
        ],
    )
    assert process.run(conn, NOW) == 3
    day = digest.yesterday(NOW + timedelta(days=1))
    assert day == NOW.date()
    grouped = digest.stories(conn, day)
    assert len(grouped["espana"]) == 1
    assert grouped["espana"][0]["level"] == rank.CONFIRMADO
    text = digest.render(grouped, day)
    assert "[CONFIRMADO]" in text and "(2 fuentes)" in text
    assert text.index("España") < text.index("Torneos")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("RADIO_DB", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("INGEST_TOKEN", "secreto")
    from radio.server import app

    return TestClient(app)


def test_ingest_requires_token_and_saves(api):
    post = {"url": "https://x.com/a/status/1", "author": "a", "text": "hola", "published_at": NOW.isoformat()}
    assert api.post("/ingest/x", json=[post]).status_code == 401
    r = api.post("/ingest/x", json=[post], headers={"Authorization": "Bearer secreto"})
    assert r.json() == {"received": 1, "new": 1}


def test_fetch_retries_once_after_429(monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(request.url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, content=RSS)

    monkeypatch.setattr(feeds.time, "sleep", lambda s: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert feeds.fetch("https://www.reddit.com/r/x/new/.rss", client, retry_after_429=True) == RSS
        assert len(calls) == 2
        calls.clear()
        assert feeds.fetch("https://news.example/rss", client) is None  # sin reintento fuera de Reddit


def test_non_competitive_news_is_left_out():
    collab = [item("Fortnite Confirms One Piece Collab With New Teaser Trailer")]
    fncs = [item("Fortnite Solo FNCS 2026 Grand Finals: Live results")]
    assert not rank.relevant(collab, [])
    assert rank.section(rank.CONFIRMADO, "", set(), rank.relevant(collab, [])) == "fuera"
    assert rank.relevant(fncs, [])
    assert rank.relevant([item("Peterbot stream highlights")], [Entity(name="Peterbot")])


def test_brand_name_alone_does_not_match_fortnite_org():
    m = Matcher([Entity(name="Fortnite", kind="org", x="FortniteGame", handle_only=True)])
    assert m.find("Fortnite anuncia una nueva skin") == []
    assert m.find("nueva temporada", author="FortniteGame")


def test_digest_skips_discarded_stories():
    conn = db.connect(":memory:")
    db.save_items(conn, [item("Fortnite Confirms One Piece Collab"), item("Cash Cup EU: resultados", author="AS")])
    process.run(conn, NOW)
    grouped = digest.stories(conn, NOW.date())
    text = digest.render(grouped, NOW.date())
    assert "One Piece" not in text and "Cash Cup" in text


def test_day_window_runs_from_6_to_6_in_madrid():
    from datetime import date

    # La hora cambia la madrugada del 25/10/2026: el día 24 empieza en verano y acaba en invierno.
    start, end = digest.day_window(date(2026, 10, 24))
    assert start == datetime(2026, 10, 24, 4, tzinfo=timezone.utc)  # 6:00 CEST
    assert end == datetime(2026, 10, 25, 5, tzinfo=timezone.utc)  # 6:00 CET
    # A las 6:30 del lunes, "ayer" es el domingo; a las 5:00 todavía es el sábado.
    assert digest.yesterday(datetime(2026, 10, 26, 5, 30, tzinfo=timezone.utc)) == date(2026, 10, 25)
    assert digest.yesterday(datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)) == date(2026, 10, 24)


def test_monday_covers_the_weekend():
    from datetime import date

    monday_morning = datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc)
    assert digest.covered_days(monday_morning) == (date(2026, 10, 23), date(2026, 10, 25))
    tuesday_morning = datetime(2026, 10, 27, 6, 30, tzinfo=timezone.utc)
    assert digest.covered_days(tuesday_morning) == (date(2026, 10, 26), date(2026, 10, 26))


def test_week_highlights_exclude_what_is_already_told():
    conn = db.connect(":memory:")
    db.save_items(
        conn,
        [
            item("Peterbot gana la FNCS Major 3", author="Dexerto", hours_ago=72),
            item("Cash Cup EU: resultados", author="AS", hours_ago=1),
        ],
    )
    process.run(conn, NOW)
    grouped = digest.stories(conn, NOW.date())
    weekly = digest.week(conn, NOW.date(), grouped)
    assert [s["headline"] for s in weekly] == ["Peterbot gana la FNCS Major 3"]
    text = digest.render(grouped, NOW.date(), weekly=weekly)
    assert text.index("Cash Cup") < text.index("Lo más importante de la semana en torneos") < text.index("Peterbot")


def test_liquipedia_region_change_and_spaniards_abroad():
    assert liquipedia.region_code("North America Central") == "NAC"
    assert liquipedia.region_code("NA West") == "NAW"
    assert liquipedia.region_code("Europe") == "EU"
    assert liquipedia.region_code("") == "EU"

    class FakeClient:
        region = "Europe"

        def category(self, title):
            return ["Zyrex"] if "Players" in title else []

        def wikitext(self, titles):
            return {t: WIKITEXT.replace("|team=", f"|region={self.region}\n|team=") for t in titles}

    conn = db.connect(":memory:")
    fake = FakeClient()
    assert liquipedia.sync(conn, fake) == []
    fake.region = "North America Central"
    [change] = liquipedia.sync(conn, fake)
    assert change.title == "Zyrex cambia de región: deja Europa y se va a competir en Norteamérica Central"

    from radio.entities import load_all

    away = digest.abroad(load_all(conn))
    assert [e.name for e in away] == ["Zyrex"]
    text = digest.render({}, date(2026, 10, 25), away=away)
    assert "Españoles compitiendo fuera de Europa" in text and "Zyrex (NAC, Team Heretics)" in text


def test_region_move_news_is_competitive():
    story = [item("Top EU pro moves to NA to compete next season", author="Dexerto")]
    tags = set().union(*(rank.tags(i.body) for i in story))
    assert "region" in tags
    assert rank.relevant(story, [])
    assert rank.section(rank.PRENSA, "", tags) == "mundo"
