#!/usr/bin/env python3
"""Link the residential-class school identities to the Residential Schools
Locations Dataset (Rosa Orlandini, Borealis doi:10.5683/SP2/FJG5TG — the
TRC/NCTR school list with IRSSA year spans, alternate names, coordinates).

Succeeded-by / same-institution evidence, never sameAs: the DIA printed one
name per statement row and the NCTR names the institution as the settlement
knew it, so "Fraser Lake" (reserve Lejac) → Lejac, "Qu'Appelle" → Lebret,
"Moose Fort" → Bishop Horden Hall need an alias table that grows by review.

Writes
  registries/crosswalks/schools_irs.csv         school_id, irs_school_id, tier, score, our_name, irs_name, evidence
  registries/crosswalks/schools_irs_review.csv  unmatched residential identities and unmatched IRS schools per era

Tiers: T1 exact key; T2 token containment with province agreement; T3 reserve
or agency field matches an IRS name; A = curated alias.
"""
import csv
import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
IRS = ROOT / "registries/external/schools/irs_locations/IRS_Locations.shp"
OUT = ROOT / "registries/crosswalks/schools_irs.csv"
REVIEW = ROOT / "registries/crosswalks/schools_irs_review.csv"

# DIA printed name -> NCTR primary name, where no rule reaches it
ALIASES = {
    "qu appelle": "Lebret", "fraser lake": "Lejac", "moose fort": "Bishop Horden Hall",
    "moose factory": "Bishop Horden Hall", "crooked lake": "Marieval", "cowessess": "Marieval",
    "stony": "Morley", "stoney": "Morley", "wabasca": "Desmarais", "kitamaat": "Kitimaat",
    "kitamaat home": "Kitimaat", "ile a la crosse": "Beauval", "lac la plonge": "Beauval",
    "hobbema": "Ermineskin", "peigan r c": "Sacred Heart", "peigan": "Sacred Heart",
    "st paul s blood": "St. Paul's", "blood c e": "St. Paul's", "blood r c": "St. Mary's",
    "old sun s": "Old Sun", "crowfoot": "Crowfoot", "sturgeon landing": "Sturgeon Landing",
    "regina": "Regina", "st boniface": "St. Boniface", "high river": "Dunbow", "dunbow": "Dunbow",
    "red deer": "Red Deer", "calgary": "Calgary", "clayoquot": "Christie", "kakawis": "Christie",
    "st george s": "St. George's", "lytton": "St. George's", "cranbrook": "Cranbrook",
    "st eugene": "Cranbrook", "kootenay": "Cranbrook", "coqualeetza": "Coqualeetza",
    "port simpson": "Port Simpson", "crosby": "Port Simpson", "metlakatla": "Metlakatla",
    "metlakahtla": "Metlakatla", "st michael s": "St. Michael's", "duck lake": "St. Michael's",
    "thunderchild": "Thunderchild", "delmas": "Thunderchild", "onion lake r c": "Onion Lake",
    "onion lake c e": "Onion Lake", "st barnabas": "Onion Lake", "battleford": "Battleford",
    "muscowequan": "Muscowequan", "muscowequan s": "Muscowequan", "gordon s": "Gordon's",
    "round lake": "Round Lake", "file hills": "File Hills", "elkhorn": "Elkhorn", "brandon": "Brandon",
    "portage la prairie": "Portage la Prairie", "birtle": "Birtle", "pine creek": "Pine Creek",
    "sandy bay": "Sandy Bay", "fort alexander": "Fort Alexander", "norway house": "Norway House",
    "cross lake": "Cross Lake", "mackay": "MacKay", "the pas": "MacKay", "shoal lake": "Cecilia Jeffrey",
    "cecilia jeffrey": "Cecilia Jeffrey", "kenora": "St. Mary's", "rat portage": "St. Mary's",
    "fort frances": "Fort Frances", "mcintosh": "McIntosh", "sioux lookout": "Pelican Lake",
    "pelican lake": "Pelican Lake", "chapleau": "Chapleau", "spanish": "Spanish Boys School",
    "spanish boys": "Spanish Boys School", "spanish girls": "Spanish Girls School",
    "shingwauk home": "Shingwauk", "wawanosh": "Wawanosh Home", "mount elgin": "Mount Elgin",
    "mount elgin institute": "Mount Elgin", "mohawk institute": "Mohawk Institute",
    "mohawk": "Mohawk Institute", "albany mission": "St. Anne's", "fort albany": "St. Anne's",
    "shubenacadie": "Shubenacadie", "carcross": "Carcross", "chooutla": "Carcross",
    "whitehorse": "Whitehorse Baptist", "moosehide": "Moosehide", "hay river": "Hay River",
    "st peter s": "Hay River", "fort providence": "Fort Providence", "fort resolution": "Fort Resolution",
    "st albert": "St. Albert", "youville": "St. Albert", "blue quills": "Blue Quills",
    "saddle lake": "Blue Quills", "sacred heart": "Sacred Heart", "holy angels": "Holy Angels",
    "fort chipewyan": "Holy Angels", "lesser slave lake": "Lesser Slave Lake", "st bernard s": "Grouard",
    "grouard": "Grouard", "st bruno s": "Joussard", "joussard": "Joussard", "whitefish lake": "Whitefish Lake",
    "st andrew s": "Whitefish Lake", "sturgeon lake": "Sturgeon Lake", "st francis xavier": "Sturgeon Lake",
    "edmonton": "Edmonton", "st albert boys": "St. Albert", "sarcee": "Sarcee", "st john s": "Sarcee",
    "ermineskin s": "Ermineskin", "morley": "Morley", "st paul s": "St. Paul's",
    "alert bay": "Alert Bay", "alert bay girls home": "Alert Bay", "ahousaht": "Ahousaht", "ahousat": "Ahousaht",
    "alberni": "Alberni", "kuper island": "Kuper Island", "kamloops": "Kamloops", "williams lake": "Cariboo",
    "cariboo": "Cariboo", "st joseph s mission": "Cariboo", "sechelt": "Sechelt", "squamish": "St. Paul's",
    "st mary s mission": "St. Mary's", "yale all hallows": "All Hallows", "all hallows": "All Hallows",
    "lower post": "Lower Post", "stuart lake": "Lejac", "beauval": "Beauval", "prince albert": "Prince Albert",
    "emmanuel college": "Prince Albert", "lac la ronge": "Lac la Ronge", "st alban s": "Prince Albert",
    "crowstand": "Crowstand", "fort pelly": "St. Philip's", "st philip s": "St. Philip's",
    "cote": "Crowstand", "guy": "Sturgeon Landing", "assiniboia": "Assiniboia", "lestock": "Muscowequan",
    "st michael s duck lake": "St. Michael's", "marieval": "Marieval", "grayson": "Marieval",
    "st joseph s": "Cariboo", "regina industrial": "Regina", "calgary industrial": "Calgary",
    "fort william": "Fort William", "st joseph s orphanage": "Fort William",
}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(indian|residential|industrial|boarding|school|institute|home|mission|"
               r"r\.?c\.?|c\.?e\.?|the|of|st|ste|saint|for|girls|boys)\b", " ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


PROV = {"ON": "ONTARIO", "QC": "QUEBEC", "NS": "NOVA SCOTIA", "NB": "NEW BRUNSWICK", "MB": "Prairies/NWT",
        "SK": "Prairies/NWT", "AB": "Prairies/NWT", "NT": "Prairies/NWT", "NU": "Prairies/NWT",
        "BC": "BRITISH COLUMBIA", "YK": "YUKON", "YT": "YUKON"}


def main():
    g = gpd.read_file(IRS)
    irs = g[["SchoolID", "NRCNamePri", "AltNames", "NameSecond", "ThirdName", "IRSSAEarYR", "IRSSALstYR",
             "Province", "ReligiousA", "IndigComm", "Municip", "Latitude", "Longitude"]].drop_duplicates("SchoolID").copy()
    irs["pool"] = irs.Province.map(lambda p: PROV.get(str(p).strip().upper(), ""))
    irs["keys"] = irs.apply(lambda r: {k for k in
                                       [norm(r.NRCNamePri), norm(r.NameSecond), norm(r.ThirdName)] +
                                       [norm(a) for a in str(r.AltNames or "").split(";")] if len(k) > 2}, axis=1)
    # several IRS schools share a primary name (St. Paul's: Blood AB and
    # Squamish BC; St. Mary's: Blood AB, Mission BC, Kenora ON), so an alias
    # resolves within the identity's province pool first
    by_primary = {}
    for r in irs.itertuples():
        by_primary.setdefault(norm(r.NRCNamePri), []).append(r)

    def alias_target(name, pool):
        k = norm(name)
        cands = by_primary.get(k, [])
        if not cands:
            # "Christie (First Location)", "Alert Bay" as an alternate name of
            # St. Michael's: containment over primary and alternate names
            tk = set(k.split())
            cands = [c for c in irs.itertuples()
                     if any(tk and (tk <= set(x.split()) or set(x.split()) <= tk) for x in c.keys)]
        same = [c for c in cands if c.pool == pool]
        return (same or cands or [None])[0]

    s = pd.read_parquet(ROOT / "registries/entities/schools.parquet")
    att = pd.read_parquet(ROOT / "registries/annotations/school_attestations.parquet")
    res = s[s.type_class == "residential"].copy()
    names = att.groupby("school_id").name_as_printed.agg(lambda x: sorted(set(x)))
    reserves = att.groupby("school_id").reserve.agg(lambda x: sorted({str(v) for v in x if v}))
    agencies = att.groupby("school_id").agency.agg(lambda x: sorted({str(v) for v in x if v}))

    links, review = [], []
    for r in res.itertuples():
        keys = {norm(n) for n in names.get(r.school_id, [r.name])}
        keys.discard("")
        cand = irs[(irs.pool == r.province_pool) | (irs.pool == "") | (r.province_pool == "")]
        hit = None
        # A: curated alias
        aliased = False
        for k in keys:
            if k in ALIASES:
                aliased = True
                c = alias_target(ALIASES[k], r.province_pool)
                if c is not None:
                    hit = (c, "A", 1.0, f"alias {k} -> {ALIASES[k]}")
                    break
        if aliased and not hit:
            # the alias names a school the IRSSA list does not carry (Calgary,
            # Regina, Dunbow closed before 1920): stop, do not fall through to
            # the reserve/agency field, which linked Calgary to St. Mary's
            review.append(dict(queue="alias_not_in_irs", school_id=r.school_id, name=r.name, province=r.province_pool,
                               years=f"{r.first_year}-{r.last_year}", n_years=r.n_years,
                               variants=" | ".join(names.get(r.school_id, [])[:5])))
            continue
        # T1: exact key
        if not hit:
            exact = [c for c in cand.itertuples() if keys & c.keys]
            exact.sort(key=lambda c: c.pool != r.province_pool)
            if exact:
                c = exact[0]
                hit = (c, "T1", 1.0, f"exact {sorted(keys & c.keys)[0]}")
        # T2: token containment, both directions, province agreeing
        if not hit:
            for c in cand.itertuples():
                for k in keys:
                    tk = set(k.split())
                    for ck in c.keys:
                        ct = set(ck.split())
                        if tk and ct and (tk <= ct or ct <= tk) and c.pool == r.province_pool:
                            hit = (c, "T2", 0.8, f"contain {k} ~ {ck}")
                            break
                    if hit:
                        break
                if hit:
                    break
        # T3: the reserve or agency printed beside the school names the IRS school
        if not hit:
            side = {norm(v) for v in reserves.get(r.school_id, []) + agencies.get(r.school_id, [])}
            side.discard("")
            for c in cand.itertuples():
                if side & c.keys:
                    hit = (c, "T3", 0.7, f"reserve/agency {sorted(side & c.keys)[0]}")
                    break
        if hit:
            c, tier, score, ev = hit
            links.append(dict(school_id=r.school_id, irs_school_id=int(c.SchoolID), tier=tier, score=score,
                              our_name=r.name, irs_name=c.NRCNamePri, irs_years=f"{c.IRSSAEarYR}-{c.IRSSALstYR}",
                              our_years=f"{r.first_year}-{r.last_year}", province=r.province_pool, evidence=ev))
        else:
            review.append(dict(queue="unmatched_ours", school_id=r.school_id, name=r.name, province=r.province_pool,
                               years=f"{r.first_year}-{r.last_year}", n_years=r.n_years,
                               variants=" | ".join(names.get(r.school_id, [])[:5])))
    linked_irs = {l["irs_school_id"] for l in links}
    for c in irs.itertuples():
        if int(c.SchoolID) not in linked_irs and c.IRSSAEarYR <= 1930:
            review.append(dict(queue="unmatched_irs", school_id="", name=c.NRCNamePri, province=c.Province,
                               years=f"{c.IRSSAEarYR}-{c.IRSSALstYR}", n_years="", variants=str(c.AltNames or "")[:80]))

    pd.DataFrame(links).to_csv(OUT, index=False)
    pd.DataFrame(review).to_csv(REVIEW, index=False)
    ldf = pd.DataFrame(links)
    print(f"residential identities: {len(res):,} | linked: {len(ldf):,} "
          f"({ldf.irs_school_id.nunique()} distinct IRS schools of {len(irs)}) | "
          f"tiers: {dict(ldf.tier.value_counts())}")
    print(f"review: {sum(1 for x in review if x['queue']=='unmatched_ours')} of ours unmatched, "
          f"{sum(1 for x in review if x['queue']=='unmatched_irs')} IRS schools (open by 1930) unmatched")


if __name__ == "__main__":
    main()
