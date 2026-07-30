/** Access to the local API. A GET when `body` is omitted, a JSON POST otherwise. */

export async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Error ${res.status}`);
  return data;
}

export const mediaUrl = (id, part) => `/api/media/${encodeURIComponent(id)}/${part}`;
