// SPDX-License-Identifier: Apache-2.0
// The ticket triager on Microsoft Agent Framework for .NET, as a hosted agent (ADR 0080).
//
// The OpenAI chat client is pointed at the platform's model (`OPENAI_BASE_URL`, the loopback key),
// and the agent is given the tools of one MCP client per MCP server of the step
// (`CHOREGOS_MCP_URL<name>`, streamable HTTP).

using System.ClientModel;
using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;
using OpenAI;

static class Agent
{
    const string Instructions =
        "You triage tickets. Use the tools to read the ticket, then answer one JSON object: "
        + "{\"status\": \"done\", \"summary\": \"<one sentence>\"}.";

    public static async Task<string> Answer(string text, CancellationToken cancellation)
    {
        var chat = new OpenAIClient(
                new ApiKeyCredential(Environment.GetEnvironmentVariable("OPENAI_API_KEY") ?? ""),
                new OpenAIClientOptions { Endpoint = new Uri(Environment.GetEnvironmentVariable("OPENAI_BASE_URL")!) })
            .GetChatClient(Environment.GetEnvironmentVariable("CHOREGOS_MODEL") ?? "default");
        var names = (Environment.GetEnvironmentVariable("CHOREGOS_MCP_SERVERS") ?? "")
            .Split(',', StringSplitOptions.RemoveEmptyEntries);
        var clients = new List<McpClient>();
        try
        {
            var tools = new List<AITool>();
            foreach (var name in names)
            {
                var transport = new HttpClientTransport(new HttpClientTransportOptions
                {
                    Name = name,
                    Endpoint = new Uri(Environment.GetEnvironmentVariable("CHOREGOS_MCP_URL") + name),
                    TransportMode = HttpTransportMode.StreamableHttp,
                });
                var client = await McpClient.CreateAsync(transport, cancellationToken: cancellation);
                clients.Add(client);
                tools.AddRange(await client.ListToolsAsync(cancellationToken: cancellation));
            }
            var agent = chat.AsAIAgent(instructions: Instructions, name: "triager", tools: tools);
            var response = await agent.RunAsync(text, cancellationToken: cancellation);
            return response.Text;
        }
        finally
        {
            foreach (var client in clients) await client.DisposeAsync();
        }
    }
}
