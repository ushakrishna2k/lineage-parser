from langgraph.graph import StateGraph, END
from typing import TypedDict
#from langchain.chat_models import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from lineage_parser import parse_lineage
from pbi_lineage_parser import parse_pbi_lineage
# Initialize model

# -------------------------
# ✅ State Definition
# -------------------------
class ParserState(TypedDict):
    code: str
    code_type: str
    result: dict

# -------------------------
# ✅ LLM Setup
# -------------------------
#llm = ChatOpenAI(model="gpt-4", temperature=0)
llm = ChatOllama(
    model="llama3",   # make sure you pulled it: ollama pull llama3
    temperature=0
)

# -------------------------
# ✅ Classifier Node
# -------------------------
def classify_code(state: ParserState) -> ParserState:
    code = state["code"]

    # Simple heuristics first (fast + cheap)
    if "SELECT" in code.upper() or "JOIN" in code.upper():
        state["code_type"] = "sql"

    elif "let" in code and "in" in code:
        state["code_type"] = "powerbi"

    else:
        # fallback to LLM classification if unclear
        prompt = f"""
        Classify the following code:
        - SQL
        - POWERBI
        - OTHER

        Code:
        {code}

        Answer ONLY one word: SQL, POWERBI, or OTHER
        """

        response = llm.invoke(prompt).content.strip().lower()

        if "sql" in response:
            state["code_type"] = "sql"
        elif "powerbi" in response:
            state["code_type"] = "powerbi"
        else:
            state["code_type"] = "other"

    return state

# -------------------------
# ✅ SQL Node
# -------------------------
def sql_parser_node(state: ParserState) -> ParserState:
    result = parse_lineage(state["code"])
    state["result"] = result
    return state

# -------------------------
# ✅ Power BI Node
# -------------------------
def powerbi_parser_node(state: ParserState) -> ParserState:
    result = parse_pbi_lineage(state["code"])
    state["result"] = result
    return state

# -------------------------
# ✅ LLM Parsing Node
# -------------------------
def llm_parser_node(state: ParserState) -> ParserState:
    code = state["code"]

    prompt = f"""
    Parse the following code and extract lineage.

    Code:
    {code}

    Return structured JSON:
    {{
      "sources": [],
      "targets": [],
      "transformations": []
    }}
    """

    response = llm.invoke(prompt).content

    state["result"] = {"type": "llm", "parsed": response}

    return state

# -------------------------
# ✅ Router Function
# -------------------------
def router(state: ParserState):
    return state["code_type"]

# -------------------------
# ✅ Build Graph
# -------------------------
builder = StateGraph(ParserState)

builder.add_node("classify", classify_code)
builder.add_node("sql_parser", sql_parser_node)
builder.add_node("powerbi_parser", powerbi_parser_node)
builder.add_node("llm_parser", llm_parser_node)

builder.set_entry_point("classify")

builder.add_conditional_edges(
    "classify",
    router,
    {
        "sql": "sql_parser",
        "powerbi": "powerbi_parser",
        "other": "llm_parser"
    }
)

builder.add_edge("sql_parser", END)
builder.add_edge("powerbi_parser", END)
builder.add_edge("llm_parser", END)

graph = builder.compile()

lineage = graph.invoke({"code": "SELECT * FROM customers JOIN orders ON customers.id = orders.customer_id"})
print(lineage)