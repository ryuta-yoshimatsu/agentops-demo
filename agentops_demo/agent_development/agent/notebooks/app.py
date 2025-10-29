from typing import Any, Generator, Optional, Sequence, Union, Literal
import mlflow
from databricks_langchain import (
    ChatDatabricks,
    UCFunctionToolkit,
    VectorSearchRetrieverTool,
)
from langchain_core.language_models import LanguageModelLike
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.tools import BaseTool, tool
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt.tool_node import ToolNode
from mlflow.langchain.chat_agent_langgraph import ChatAgentState, ChatAgentToolNode
from mlflow.pyfunc import ChatAgent
from mlflow.types.agent import (
    ChatAgentChunk,
    ChatAgentMessage,
    ChatAgentResponse,
    ChatContext,
)

## Load the agent's configuration
model_config = mlflow.models.ModelConfig(development_config="config.yaml")

uc_catalog = model_config.get("uc_catalog")
schema = model_config.get("schema")
vector_search_index = model_config.get("vector_search_index")

python_execution_function_name = f"{uc_catalog}.{schema}.execute_python_code"
ask_ai_function_name = f"{uc_catalog}.{schema}.ask_ai"
summarization_function_name = f"{uc_catalog}.{schema}.summarize"
translate_function_name = f"{uc_catalog}.{schema}.translate"

@tool
def retrieve_function(query: str) -> str:
    """Retrieve from Databricks Vector Search using the query."""

    index = f"{uc_catalog}.{schema}.{vector_search_index}"

    vs_tool = VectorSearchRetrieverTool(
        index_name=index,
        tool_name="vector_search_retriever",
        tool_description="Retrieves information from Databricks Vector Search.",
        embedding_model_name="databricks-bge-large-en", 
        num_results=1, 
        columns=["url", "content"],
        query_type="ANN" 
    )

    response = vs_tool.invoke(query)
    return f"{response[0].metadata['url']}  \n{response[0].page_content}"
  
toolkit = UCFunctionToolkit(
  function_names=[
    python_execution_function_name,
    # ask_ai_function_name, # commenting out to showcase retriever
    summarization_function_name,
    translate_function_name,
    ]
)
uc_tools = toolkit.tools
tools = uc_tools + [retrieve_function]

# Example for Databricks foundation model endpoints
model = ChatDatabricks(endpoint="databricks-meta-llama-3-3-70b-instruct")
system_prompt = "You are a Databricks expert. "

def create_tool_calling_agent(
    model: LanguageModelLike, 
    tools: Union[ToolNode, Sequence[BaseTool]], 
    system_prompt: Optional[str]=None
): 
    model = model.bind_tools(tools)

    def should_continue(state: ChatAgentState) -> Literal["tools", END]:
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.get("tool_calls"):
            return "tools"
        return END

    preprocessor = RunnableLambda(lambda state: [{"role": "system", "content": system_prompt}] + state["messages"])
    model_runnable = preprocessor | model

    def call_model(state: ChatAgentState, config: RunnableConfig):
        failing = True
        retry = 10
        while failing and retry>=0: 
            try: 
                response = model_runnable.invoke(state, config)
                failing = False
            except: 
                retry -= 1
        return {"messages": [response]}

    workflow = StateGraph(ChatAgentState)

    tool_node = ChatAgentToolNode(tools)
    workflow.add_node("agent", RunnableLambda(call_model))
    workflow.add_node("tools", tool_node)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    return workflow.compile()

class LangGraphChatAgent(ChatAgent):
    def __init__(self, agent):
        self.agent = agent

    def predict(
        self,
        messages: list[ChatAgentMessage],
        context: Optional[ChatContext] = None,
        custom_inputs: Optional[dict[str, Any]] = None,
    ) -> ChatAgentResponse:
        request = {"messages": self._convert_messages_to_dict(messages)}

        messages = []
        for event in self.agent.stream(request, stream_mode="updates"):
            for node_data in event.values():
                messages.extend(
                    ChatAgentMessage(**msg) for msg in node_data.get("messages", [])
                )
        return ChatAgentResponse(messages=messages)

    def predict_stream(
        self,
        messages: list[ChatAgentMessage],
        context: Optional[ChatContext] = None,
        custom_inputs: Optional[dict[str, Any]] = None,
    ) -> Generator[ChatAgentChunk, None, None]:
        request = {"messages": self._convert_messages_to_dict(messages)}
        for event in self.agent.stream(request, stream_mode="updates"):
            for node_data in event.values():
                yield from (
                    ChatAgentChunk(**{"delta": msg}) for msg in node_data["messages"]
                )

# Create the agent object, and specify it as the agent object to use when
# loading the agent back for inference via mlflow.models.set_model()
mlflow.langchain.autolog()
agent = create_tool_calling_agent(model, tools, system_prompt)
AGENT = LangGraphChatAgent(agent)
mlflow.models.set_model(AGENT)
