import os
from pathlib import Path
import pandas as pd
import yaml
from typing import Any, Dict, List
import time
from mcp.server.fastmcp import FastMCP
import psycopg2
import json
import re
from typing import Optional, Set

# GraphRAG Query API
from graphrag.api.query import global_search, local_search
from graphrag.config.models.graph_rag_config import GraphRagConfig

# ------------------------------------------------------------------------------
# Paths / settings
# ------------------------------------------------------------------------------

ROOT = Path(os.getenv("GRAPHRAG_ROOT", "/app/graphrag-folder"))
OUTPUT = Path(os.getenv("GRAPHRAG_OUTPUT_DIR", str(ROOT / "output")))
SETTINGS_PATH = Path(os.getenv("CONFIG_PATH", str(ROOT / "settings.yaml")))

SEARCH_METHOD = os.getenv("GRAPHRAG_SEARCH_METHOD", "local").strip().lower()
if SEARCH_METHOD not in ("local", "global"):
    SEARCH_METHOD = "local"

RESPONSE_TYPE = os.getenv("GRAPHRAG_RESPONSE_TYPE", "multiple paragraphs")
COMMUNITY_LEVEL = os.getenv("GRAPHRAG_COMMUNITY_LEVEL")
DYNAMIC_COMMUNITY_SELECTION = (
    os.getenv("GRAPHRAG_DYNAMIC_COMMUNITY_SELECTION", "true").lower() == "true"
)

RELOAD_ON_EACH_REQUEST = (
    os.getenv("GRAPHRAG_RELOAD_ON_EACH_REQUEST", "false").lower() == "true"
)

# ------------------------------------------------------------------------------
# Cached state
# ------------------------------------------------------------------------------

_CONFIG = None
_FRAMES = None
_FRAMES_ERROR = None

REQUIRED_GLOBAL = ["entities", "communities", "community_reports"]
REQUIRED_LOCAL = REQUIRED_GLOBAL + ["text_units", "relationships"]

# ------------------------------------------------------------------------------
# Load GraphRAG config
# ------------------------------------------------------------------------------

def load_graphrag_config() -> GraphRagConfig:
    with open(SETTINGS_PATH, "r") as f:
        expanded = os.path.expandvars(f.read())
        return GraphRagConfig.model_validate(yaml.safe_load(expanded) or {})

# ------------------------------------------------------------------------------
# Load parquet frames
# ------------------------------------------------------------------------------

def parquet_path(name: str) -> Path:
    return OUTPUT / f"{name}.parquet"


def load_frames():
    required = REQUIRED_LOCAL if SEARCH_METHOD == "local" else REQUIRED_GLOBAL
    missing = [n for n in required if not parquet_path(n).exists()]
    if missing:
        return None, f"Missing required parquet files in {OUTPUT}: {', '.join(missing)}"

    frames = {
        "entities": pd.read_parquet(parquet_path("entities")),
        "communities": pd.read_parquet(parquet_path("communities")),
        "community_reports": pd.read_parquet(parquet_path("community_reports")),
    }

    if SEARCH_METHOD == "local":
        frames["text_units"] = pd.read_parquet(parquet_path("text_units"))
        frames["relationships"] = pd.read_parquet(parquet_path("relationships"))
        cov_path = parquet_path("covariates")
        frames["covariates"] = pd.read_parquet(cov_path) if cov_path.exists() else None

    return frames, None


def ensure_loaded():
    global _CONFIG, _FRAMES, _FRAMES_ERROR
    if RELOAD_ON_EACH_REQUEST or _CONFIG is None:
        _CONFIG = load_graphrag_config()
    if RELOAD_ON_EACH_REQUEST or _FRAMES is None:
        _FRAMES, _FRAMES_ERROR = load_frames()

def resolve_community_level() -> int:
    if COMMUNITY_LEVEL:
        try:
            return int(COMMUNITY_LEVEL)
        except ValueError:
            pass
    return 2

def filter_communities(communities: pd.DataFrame, level: int):
    if "level" not in communities.columns:
        return communities
    return communities[communities["level"] == level]

ALLOWED_LOCAL_PARAMS = {
    "community_level",
    "response_type",
}

ALLOWED_GLOBAL_PARAMS = {
    "community_level",
    "response_type",
    "dynamic_community_selection",
}


# ------------------------------------------------------------------------------
# GraphRAG Query wrapper (API ONLY)
# ------------------------------------------------------------------------------

async def run_graphrag(
    query: str,
    search_method: str = "local",
    params: dict | None = None,
) -> str:
    ensure_loaded()
    if _FRAMES_ERROR:
        return f"(error) {_FRAMES_ERROR}"

    params = params or {}
    cl = params.get("community_level", resolve_community_level())

    # filter communities explicitly (important for local)
    all_communities = _FRAMES["communities"]

    local_communities = (
        all_communities
        if "level" not in all_communities.columns
        else all_communities[all_communities["level"] == cl]
    )

    if search_method == "global":
        filtered = {k: v for k, v in params.items() if k in ALLOWED_GLOBAL_PARAMS}

        response, _ = await global_search(
            config=_CONFIG,
            entities=_FRAMES["entities"],
            communities=all_communities,   # ✅ IMPORTANT: unfiltered
            community_reports=_FRAMES["community_reports"],
            community_level=filtered.get("community_level", cl),
            dynamic_community_selection=filtered.get(
                "dynamic_community_selection", DYNAMIC_COMMUNITY_SELECTION
            ),
            response_type=filtered.get("response_type", RESPONSE_TYPE),
            query=query,
        )
        return response

    # ---- local search ----
    filtered = {k: v for k, v in params.items() if k in ALLOWED_LOCAL_PARAMS}

    response, _ = await local_search(
        config=_CONFIG,
        entities=_FRAMES["entities"],
        communities=local_communities,    # ✅ filtered only for local
        community_reports=_FRAMES["community_reports"],
        text_units=_FRAMES["text_units"],
        relationships=_FRAMES["relationships"],
        covariates=_FRAMES.get("covariates"),
        community_level=filtered.get("community_level", cl),
        response_type=filtered.get("response_type", RESPONSE_TYPE),
        query=query,
    )
    return response

# ------------------------------------------------------------------------------
# AGE (Apache AGE) helpers 
# ------------------------------------------------------------------------------
AGE_GRAPH_NAME = os.getenv("AGE_GRAPH_NAME", "graphRAG")

_SCHEMA_CACHE = {"value": None, "ts": 0.0}
_SCHEMA_TTL_SECONDS = int(os.getenv("AGE_SCHEMA_TTL_SECONDS", "3600"))

def get_conn():
    host = os.getenv("AGE_HOST") if os.getenv("USE_LOCAL_AGE") == "true" else os.getenv("POSTGRES_HOST")
    port = os.getenv("AGE_PORT") if os.getenv("USE_LOCAL_AGE") == "true" else os.getenv("POSTGRES_PORT")
    user = os.getenv("AGE_USER") if os.getenv("USE_LOCAL_AGE") == "true" else os.getenv("POSTGRES_USER")
    password = os.getenv("AGE_PASSWORD") if os.getenv("USE_LOCAL_AGE") == "true" else os.getenv("POSTGRES_PASSWORD")
    dbname = os.getenv("AGE_DB") if os.getenv("USE_LOCAL_AGE") == "true" else os.getenv("POSTGRES_DB")

    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname
    )


def _prepare_age(cur):
    cur.execute("LOAD 'age';")
    cur.execute("SET search_path = ag_catalog, public;")

def get_age_schema() -> Dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            _prepare_age(cur)
            cur.execute(
                """
                SELECT DISTINCT label
                FROM cypher(%s, $$ MATCH (n) RETURN labels(n)[0] AS label $$)
                AS (label agtype);
                """,
                (AGE_GRAPH_NAME,),
            )
            vertex_labels = sorted({r[0] for r in cur.fetchall() if r[0]})

            cur.execute(
                """
                SELECT DISTINCT label
                FROM cypher(%s, $$ MATCH ()-[r]->() RETURN type(r) AS label $$)
                AS (label agtype);
                """,
                (AGE_GRAPH_NAME,),
            )
            edge_labels = sorted({r[0] for r in cur.fetchall() if r[0]})

    return {
        "graph": AGE_GRAPH_NAME,
        "vertex_labels": vertex_labels,
        "edge_labels": edge_labels,
    }


def get_age_schema_cached(refresh: bool = False) -> dict:
    """
    Cached schema fetch. Use refresh=True to force reload.
    TTL controlled by AGE_SCHEMA_TTL_SECONDS.
    """
    now = time.time()
    expired = (_SCHEMA_CACHE["value"] is None) or ((now - _SCHEMA_CACHE["ts"]) > _SCHEMA_TTL_SECONDS)

    if refresh or expired:
        schema = get_age_schema()
        _SCHEMA_CACHE["value"] = schema
        _SCHEMA_CACHE["ts"] = now
        return {
            "tool": "age_get_schema_cached",
            "refresh": True,
            "cached": False,
            "ttl_seconds": _SCHEMA_TTL_SECONDS,
            "result": schema,
        }

    return {
        "tool": "age_get_schema_cached",
        "refresh": False,
        "cached": True,
        "ttl_seconds": _SCHEMA_TTL_SECONDS,
        "result": _SCHEMA_CACHE["value"],
    }

def _normalize_age_label(label: str) -> str:
    """
    AGE does NOT allow quoted labels in MATCH.
    Schema may return '"Entity"' so we normalize it to Entity.
    """
    if label.startswith('"') and label.endswith('"'):
        return label[1:-1]
    return label


def get_age_schema_details(sample_limit: int = 1) -> dict:
    schema = get_age_schema()

    with get_conn() as conn:
        with conn.cursor() as cur:
            _prepare_age(cur)

            node_props = {}
            for raw_lbl in schema["vertex_labels"]:
                lbl = _normalize_age_label(raw_lbl)

                cur.execute(
                    f"""
                    SELECT keys
                    FROM cypher(%s, $$
                        MATCH (n:{lbl})
                        RETURN keys(n) AS keys
                        LIMIT {sample_limit}
                    $$) AS (keys agtype);
                    """,
                    (AGE_GRAPH_NAME,)
                )

                rows = cur.fetchall()
                node_props[raw_lbl] = list(
                    {r[0] for r in rows if r and r[0]}
                )

            rel_props = {}
            for raw_rel in schema["edge_labels"]:
                rel = _normalize_age_label(raw_rel)

                cur.execute(
                    f"""
                    SELECT keys
                    FROM cypher(%s, $$
                        MATCH ()-[r:{rel}]->()
                        RETURN keys(r) AS keys
                        LIMIT {sample_limit}
                    $$) AS (keys agtype);
                    """,
                    (AGE_GRAPH_NAME,)
                )

                rows = cur.fetchall()
                rel_props[raw_rel] = list(
                    {r[0] for r in rows if r and r[0]}
                )

    return {
        "tool": "age_get_schema_details",
        "graph": schema["graph"],
        "vertex_labels": schema["vertex_labels"],
        "edge_labels": schema["edge_labels"],
        "node_property_keys": node_props,
        "edge_property_keys": rel_props,
    }    


def run_cypher(cypher: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT *
    FROM cypher(%s, $$ {cypher} $$)
    AS (result agtype);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            _prepare_age(cur)
            cur.execute(sql, (AGE_GRAPH_NAME,))
            rows = cur.fetchall()
    return [{"result": r[0]} for r in rows]

def age_entity_lookup_impl(name: str, limit: int = 5) -> dict:
    safe = (name or "").lower().replace("'", " ")
    limit = int(limit) if limit else 5
    limit = max(1, min(limit, 20))

    cypher = f"""
    MATCH (e:Entity)
    WHERE toLower(e.title) CONTAINS '{safe}'
    RETURN {{
      title: e.title,
      type: e.type,
      description: e.description
    }} AS result
    LIMIT {limit}
    """

    rows = run_cypher(cypher)
    return {
        "tool": "age_entity_lookup",
        "input": {"name": name, "limit": limit},
        "row_count": len(rows),
        "rows": rows,
    }


def _get_aoai_client():
    endpoint = os.getenv("AOAI_API_BASE")
    api_key  = os.getenv("AOAI_API_KEY")
    deployment = os.getenv("AOAI_LLM_DEPLOYMENT")
    api_version = os.getenv("AOAI_LLM_API_VERSION")

    if not endpoint or not api_key or not deployment:
        raise RuntimeError(
            "Missing AOAI env vars. Need endpoint+key+deployment. "
            "Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT (or equivalents)."
        )

    # Lazy import to avoid crashing tool discovery if package is missing
    from openai import AzureOpenAI
    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )
    return client, deployment


def _aoai_chat(system_prompt: str, user_prompt: str, temperature: float = 0.1, max_tokens: int = 800) -> str:
    client, deployment = _get_aoai_client()
    resp = client.chat.completions.create(
        model=deployment,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.choices[0].message.content or ""


# ---------- Cypher extraction + validation ----------
_CYPHER_BLOCK_RE = re.compile(r"```(?:cypher|sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

def _extract_cypher(text: str) -> str:
    m = _CYPHER_BLOCK_RE.search(text or "")
    cypher = (m.group(1) if m else (text or "")).strip()

    # If model returned SQL wrapper, extract $$ ... $$ section
    if "FROM cypher" in cypher and "$$" in cypher:
        parts = cypher.split("$$")
        if len(parts) >= 3:
            cypher = parts[1].strip()

    return cypher


_LABEL_RE = re.compile(r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")
_RELTYPE_RE = re.compile(r"\[\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")

def _normalize_schema_token(x: str) -> str:
    # schema may return '"Entity"' so strip quotes
    x = (x or "").strip()
    if len(x) >= 2 and x[0] == '"' and x[-1] == '"':
        x = x[1:-1]
    return x

def _extract_used_labels(cypher: str) -> Set[str]:
    return {m.group(1) for m in _LABEL_RE.finditer(cypher or "")}

def _extract_used_reltypes(cypher: str) -> Set[str]:
    return {m.group(1) for m in _RELTYPE_RE.finditer(cypher or "")}

def _validate_cypher(cypher: str) -> Optional[str]:
    """
    Return None if OK, else a short error string describing what to fix.
    Generic + schema-driven (no hard-coded labels/edges).
    """
    c = (cypher or "").strip()
    if not c:
        return "Empty Cypher."

    # Pull schema once (cached)
    schema_info = get_age_schema_cached(refresh=False)["result"]
    allowed_labels = {_normalize_schema_token(x) for x in schema_info.get("vertex_labels", [])}
    allowed_reltypes = {_normalize_schema_token(x) for x in schema_info.get("edge_labels", [])}

    used_labels = _extract_used_labels(c)
    used_reltypes = _extract_used_reltypes(c)

    unknown_labels = sorted([x for x in used_labels if x not in allowed_labels])
    if unknown_labels:
        return f"Unknown node labels: {unknown_labels}. Allowed labels: {sorted(allowed_labels)}."

    unknown_reltypes = sorted([x for x in used_reltypes if x not in allowed_reltypes])
    if unknown_reltypes:
        return f"Unknown relationship types: {unknown_reltypes}. Allowed relationship types: {sorted(allowed_reltypes)}."

    if "RETURN {" not in c or " AS result" not in c:
        return "Cypher must return exactly one map: RETURN { ... } AS result."

    if "LIMIT" not in c.upper():
        return "Cypher must include LIMIT."

    # Optional: generic heuristic to nudge semantic filtering to r.description instead of Entity.description
    if ".description" in c and "r.description" not in c:
        return "Cypher should filter semantic meaning on r.description (bind [r:RELATED_TO]) rather than Entity.description."

    return None


# ---------- Prompt loading ----------

def load_mcp_prompt(name: str) -> str:
    base = "/app/graphrag-folder/prompts"
    path = os.path.join(base, name)
    with open(path, "r") as f:
        return f.read()

_NL2CYPHER_SYSTEM = load_mcp_prompt("age_nl2cypher.txt")

def _nl2cypher_user_prompt(question: str, limit: int) -> str:
    # Keep this minimal. Your system prompt file carries the heavy rules.
    return f"Question: {question}\nLimit: {limit}\n"

# ---------- Tool implementation ----------

def age_nl2cypher_query_impl(question: str, limit: int = 5, max_repairs: int = 2) -> dict:
    limit = int(limit) if limit else 5
    limit = max(1, min(limit, 20))

    raw = _aoai_chat(_NL2CYPHER_SYSTEM, _nl2cypher_user_prompt(question, limit), temperature=0.1, max_tokens=800)
    cypher = _extract_cypher(raw)
    err = _validate_cypher(cypher)

    repairs = 0
    while err and repairs < max_repairs:
        repair_prompt = f"""
Your previous Cypher was invalid.

Reason: {err}

Fix it and output ONLY corrected Cypher.
Constraints:
- Only :Entity and :Document labels
- Only :RELATED_TO relationship type
- Must return: RETURN {{...}} AS result
- Must include LIMIT {limit}

Question: {question}

Previous Cypher:
{cypher}
"""
        raw = _aoai_chat(_NL2CYPHER_SYSTEM, repair_prompt, temperature=0.1, max_tokens=800)
        cypher = _extract_cypher(raw)
        err = _validate_cypher(cypher)
        repairs += 1

    if err:
        return {
            "tool": "age_nl2cypher_query",
            "input": {"question": question, "limit": limit},
            "generated_cypher": cypher,
            "error": f"Failed to generate valid Cypher after repairs: {err}",
            "row_count": 0,
            "rows": [],
        }

    rows = run_cypher(cypher)  
    return {
        "tool": "age_nl2cypher_query",
        "input": {"question": question, "limit": limit},
        "generated_cypher": cypher,
        "row_count": len(rows),
        "rows": rows,
    }


# ------------------------------------------------------------------------------
# MCP server
# ------------------------------------------------------------------------------

mcp = FastMCP(
    "GraphRAG AGE MCP",
    json_response=True,
    stateless_http=True,
)

@mcp.tool(
    name="graphrag_search",
    description="Run a GraphRAG query (local or global) with runtime-tunable API params",
)
async def graphrag_search(
    query: str,
    search_method: str = "local",
    params: dict | None = None,
) -> dict:
    result = await run_graphrag(query, search_method, params)
    print(f"[MCP] Tool invoked: graphrag_search", flush=True)

    return {
        "tool": "graphrag_search",
        "input": {
            "query": query,
            "search_method": search_method,
            "params": params,
        },
        "result": result,
    }

@mcp.tool(
    name="age_get_schema_cached",
    description="Return cached AGE graph schema; if refresh=true, re-query the database and update the cache."
)
def age_get_schema_cached(refresh: bool = False) -> dict:
    print("[MCP] Tool invoked: age_get_schema_cached", flush=True)
    return get_age_schema_cached(refresh=refresh)

@mcp.tool(
    name="age_cypher_query",
    description="Execute a user-provided Cypher query against the AGE graph and return rows (each row under key 'result')."
)
def age_cypher_query(cypher: str) -> dict:
    rows = run_cypher(cypher)
    print(f"[MCP] Tool invoked: age_cypher_query", flush=True)
    return {
        "tool": "age_cypher_query",
        "input": {"cypher": cypher},
        "row_count": len(rows),
        "rows": rows,
    }

@mcp.tool(
    name="age_entity_lookup",
    description="Find Entity nodes by name/title substring match (best for 'Who is X?' or quick disambiguation)."
)
def age_entity_lookup(name: str, limit: int = 5) -> dict:
    print("[MCP] Tool invoked: age_entity_lookup", flush=True)
    return age_entity_lookup_impl(name=name, limit=limit)


@mcp.tool(    
    name="age_nl2cypher_query",    
    description="Convert a natural-language question into a Cypher query (Entity/RELATED_TO only), execute it, and return rows; best for complex or multi-hop semantic graph questions."
)
def age_nl2cypher_query(question: str, limit: int = 5) -> dict:
    print("[MCP] Tool invoked: age_nl2cypher_query", flush=True)
    return age_nl2cypher_query_impl(question=question, limit=limit)

"""
Do not expose below tools, 
@mcp.tool(name="age_get_schema")
def age_get_schema() -> dict:
    schema = get_age_schema()
    print(f"[MCP] Tool invoked: age_get_schema", flush=True)
    return {"tool": "age_get_schema", "result": schema}

@mcp.tool("age_get_schema_details")
def age_get_schema_details(sample_limit: int = 1) -> dict:
    print("[MCP] Tool invoked: age_get_schema_details", flush=True)
    return get_age_schema_details(sample_limit=sample_limit)
"""

# ------------------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")