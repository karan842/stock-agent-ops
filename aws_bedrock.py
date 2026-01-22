from langchain_aws import ChatBedrock

llm = ChatBedrock(
    model_id="meta.llama3-70b-instruct-v1:0",
    model_kwargs={"temperature": 0.3}
)

messages = [
    (
        "system",
        "You are a helpful assistant that translates English to French. Translate the user sentence.",
    ),
    ("human", "I love programming."),
]
ai_msg = llm.invoke(messages)
print(ai_msg)