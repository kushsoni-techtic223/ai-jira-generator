const STORAGE_KEY = "jira_session_id";
export const SESSION_HEADER = "X-Jira-Session";

export function getJiraSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STORAGE_KEY);
}

export function setJiraSessionId(sid: string | null) {
  if (typeof window === "undefined") return;
  if (sid) localStorage.setItem(STORAGE_KEY, sid);
  else localStorage.removeItem(STORAGE_KEY);
}

/** Axios/fetch headers for the current browser's Jira OAuth session. */
export function jiraAuthHeaders(): Record<string, string> {
  const sid = getJiraSessionId();
  return sid ? { [SESSION_HEADER]: sid } : {};
}
