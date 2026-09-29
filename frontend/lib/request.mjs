export function createRequest(base = "", fetcher = (...args) => fetch(...args)) {
  return async function request(path, { method = "GET", body, timeoutMs = 12000 } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await fetcher(`${base}${path}`, {
        method, headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store", credentials: "include", signal: controller.signal,
      });
      let data;
      try { data = response.status === 204 ? null : await response.json(); }
      catch (error) {
        if (error?.name === "AbortError") throw error;
        const failure = new Error(response.ok ? "The server returned an unreadable response. Please try again." : `Request failed (${response.status}). Please try again.`);
        failure.status = response.status;
        throw failure;
      }
      if (!response.ok) {
        const detail = data?.error?.message || data?.detail || data?.error;
        const failure = new Error(typeof detail === "string" ? detail : `Request failed (${response.status}). Please check your inputs and try again.`);
        failure.status = response.status;
        throw failure;
      }
      return data;
    } catch (error) {
      if (error?.name === "AbortError") throw new Error("The server took too long to respond. Please try again.");
      if (response || error?.status) throw error;
      throw new Error("Unable to connect to the server. Please try again.");
    } finally {
      // Keep the timeout active until the response body finishes, not just headers.
      clearTimeout(timeout);
    }
  };
}
