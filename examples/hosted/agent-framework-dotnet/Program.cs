// SPDX-License-Identifier: Apache-2.0
// Foundry's hosted-agent contract on `$PORT`, on ASP.NET minimal APIs (ADR 0080).
//
// The .NET counterpart of `responses_server.py`: `GET /readiness`, `POST /responses` (the Responses
// API, as server-sent events when the body asks `stream: true`) and `POST /invocations` (a JSON body
// in, `{"output": ...}` out). It hands the text of the request to `Agent.Answer`, the one function
// the example writes. On SIGTERM the host stops accepting, lets the request in flight finish and
// exits 0.

using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

var builder = WebApplication.CreateBuilder(args);
// Every interface of the container: the runner reaches it on the pod's loopback.
var host = Environment.GetEnvironmentVariable("HOST") ?? "0.0.0.0";
var port = Environment.GetEnvironmentVariable("PORT") ?? "8088";
builder.WebHost.UseUrls($"http://{host}:{port}");
var app = builder.Build();

app.MapGet("/readiness", () => Results.Json(new { status = "ready" }));

app.MapPost("/invocations", async (HttpRequest request, CancellationToken cancellation) =>
{
    var body = await ReadBody(request);
    if (body is null) return Results.Json(new { error = "the body is not JSON" }, statusCode: 400);
    try
    {
        return Results.Json(new { output = await Agent.Answer(RequestText(body), cancellation) });
    }
    catch (Exception error)
    {
        Console.Error.WriteLine(error);
        return Results.Json(new { error = error.Message }, statusCode: 500);
    }
});

app.MapPost("/responses", async (HttpRequest request, CancellationToken cancellation) =>
{
    var body = await ReadBody(request);
    if (body is null) return Results.Json(new { error = "the body is not JSON" }, statusCode: 400);
    JsonObject response;
    try
    {
        response = ResponseObject(await Agent.Answer(RequestText(body), cancellation));
    }
    catch (Exception error)
    {
        Console.Error.WriteLine(error);
        var message = error.Message;
        response = ResponseObject("", "failed", message[..Math.Min(500, message.Length)]);
    }
    if (body["stream"] is not JsonValue stream || !stream.TryGetValue<bool>(out var streamed) || !streamed)
        return Results.Text(response.ToJsonString(), "application/json");
    var text = string.Concat(
        response["output"]!.AsArray().SelectMany(item => item!["content"]!.AsArray()).Select(part => (string?)part!["text"]));
    var started = (JsonObject)response.DeepClone();
    started["status"] = "in_progress";
    started["output"] = new JsonArray();
    var events = new List<JsonObject> { new() { ["type"] = "response.created", ["response"] = started } };
    if (text.Length > 0)
        events.Add(new() { ["type"] = "response.output_text.delta", ["output_index"] = 0, ["delta"] = text });
    var final = (string?)response["status"] == "completed" ? "response.completed" : "response.failed";
    events.Add(new() { ["type"] = final, ["response"] = response });
    var sse = new StringBuilder();
    for (var n = 0; n < events.Count; n++)
    {
        events[n]["sequence_number"] = n;
        sse.Append($"event: {events[n]["type"]}\ndata: {events[n].ToJsonString()}\n\n");
    }
    return Results.Text(sse.ToString(), "text/event-stream");
});

app.Run();

static async Task<JsonObject?> ReadBody(HttpRequest request)
{
    try
    {
        using var reader = new StreamReader(request.Body);
        var raw = await reader.ReadToEndAsync();
        return JsonNode.Parse(raw.Length > 0 ? raw : "{}") as JsonObject;
    }
    catch (JsonException)
    {
        return null;
    }
}

// The text of a request: `input` as a string, or the text parts of its messages.
static string RequestText(JsonObject body)
{
    var value = body["input"];
    if (value is null) return "";
    if (value is JsonValue single && single.TryGetValue<string>(out var plain)) return plain;
    var parts = new List<string>();
    foreach (var item in value as JsonArray ?? [])
    {
        var content = item is JsonObject message ? message["content"] : item;
        if (content is JsonValue part && part.TryGetValue<string>(out var said)) parts.Add(said);
        else if (content is JsonArray pieces)
            parts.AddRange(pieces.OfType<JsonObject>().Select(piece => piece["text"]?.ToString() ?? ""));
    }
    return string.Join("\n", parts.Where(part => part.Length > 0));
}

static JsonObject ResponseObject(string text, string status = "completed", string error = "")
{
    var output = new JsonArray();
    if (text.Length > 0)
        output.Add(new JsonObject
        {
            ["type"] = "message",
            ["id"] = $"msg_{Guid.NewGuid():N}",
            ["role"] = "assistant",
            ["status"] = "completed",
            ["content"] = new JsonArray(
                new JsonObject { ["type"] = "output_text", ["text"] = text, ["annotations"] = new JsonArray() }),
        });
    return new JsonObject
    {
        ["id"] = $"resp_{Guid.NewGuid():N}",
        ["object"] = "response",
        ["created_at"] = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
        ["status"] = status,
        ["model"] = Environment.GetEnvironmentVariable("CHOREGOS_MODEL") ?? "",
        ["output"] = output,
        ["error"] = error.Length > 0 ? new JsonObject { ["code"] = "server_error", ["message"] = error } : null,
    };
}
