export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const query = (searchParams.get("q") || "").trim();
  const limit = searchParams.get("limit") || "8";

  if (!query) {
    return Response.json({ results: [] }, { status: 200 });
  }

  const gatewayUrl = process.env.GATEWAY_URL || "http://localhost:8080";

  try {
    const response = await fetch(`${gatewayUrl}/search?q=${encodeURIComponent(query)}&limit=${limit}`, {
      method: "GET",
      cache: "no-store",
    });

    if (!response.ok) {
      return Response.json(
        { results: [], error: "Gateway query failed" },
        { status: 502 }
      );
    }

    const data = await response.json();
    return Response.json(data, { status: 200 });
  } catch (error) {
    return Response.json(
      { results: [], error: error?.message || "Search failed" },
      { status: 500 }
    );
  }
}

function parseNaturalQuery(rawQuery) {
  const query = (rawQuery || "").trim();
  const filters = [];
  let text = query;

  // File type filters (basic)
  const extensionMatches = [
    { pattern: /\bpdfs?\b/i, ext: "pdf" },
    { pattern: /\bimages?\b/i, ext: "image" },
    { pattern: /\bdocs?\b|\bdocuments?\b/i, ext: "document" },
  ];

  extensionMatches.forEach(({ pattern, ext }) => {
    if (pattern.test(text)) {
      if (ext === "pdf") {
        filters.push({ term: { extension: "pdf" } });
      } else if (ext === "image") {
        filters.push({ prefix: { content_type: "image" } });
      } else if (ext === "document") {
        filters.push({ prefix: { content_type: "application" } });
      }
      text = text.replace(pattern, " ");
    }
  });

  // Date filter: "May 7th" -> range filter for that day in current year
  const dateMatch = text.match(
    /\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?\b/i,
  );
  if (dateMatch) {
    const monthName = dateMatch[1].toLowerCase();
    const day = parseInt(dateMatch[2], 10);
    const monthIndex = monthToIndex(monthName);
    if (monthIndex !== null && day >= 1 && day <= 31) {
      const year = new Date().getUTCFullYear();
      const start = new Date(Date.UTC(year, monthIndex, day, 0, 0, 0));
      const end = new Date(Date.UTC(year, monthIndex, day + 1, 0, 0, 0));
      filters.push({
        range: {
          last_modified: {
            gte: start.toISOString(),
            lt: end.toISOString(),
          },
        },
      });
      text = text.replace(dateMatch[0], " ");
    }
  }

  text = text.replace(/\s+/g, " ").trim();

  return {
    queryText: text,
    filterClauses: filters,
  };
}

function monthToIndex(month) {
  const map = {
    jan: 0,
    january: 0,
    feb: 1,
    february: 1,
    mar: 2,
    march: 2,
    apr: 3,
    april: 3,
    may: 4,
    jun: 5,
    june: 5,
    jul: 6,
    july: 6,
    aug: 7,
    august: 7,
    sep: 8,
    september: 8,
    oct: 9,
    october: 9,
    nov: 10,
    november: 10,
    dec: 11,
    december: 11,
  };

  return map[month] ?? null;
}
