from openai import AzureOpenAI
from azure.identity import AzureCliCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    AzureCliCredential(),
    "https://cognitiveservices.azure.com/.default"
)

client = AzureOpenAI(
    azure_endpoint="https://circlektesting.openai.azure.com/",
    api_version="2024-12-01-preview",
    azure_ad_token_provider=token_provider
)

response = client.chat.completions.create(
    model="invoice-extractor",
    messages=[
        {"role": "user", "content": "Say hello"}
    ]
)

print(response.choices[0].message.content)