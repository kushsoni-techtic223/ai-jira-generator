"use client";

import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { JiraConnection } from "../types";

const API = "http://localhost:8000";
const STORAGE_KEY = "jira-live-connection-v1";

export const emptyConnection = (): JiraConnection => ({
  base_url: "",
  email: "",
  api_token: "",
  project_key: "",
  board_id: ""
});

export function loadSavedConnection(): JiraConnection {
  if (typeof window === "undefined") return emptyConnection();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyConnection();
    return { ...emptyConnection(), ...JSON.parse(raw) };
  } catch {
    return emptyConnection();
  }
}

export function saveConnection(conn: JiraConnection) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(conn));
}

export type ServerJiraConfig = {
  configured: boolean;
  base_url: string | null;
  email: string | null;
  project_key: string | null;
  board_id: string | null;
  has_api_token: boolean;
};

export type JiraBoardInfo = {
  id: number | string;
  name: string;
  type?: string;
  project_key?: string;
};

function connectionPayload(conn: JiraConnection) {
  const body: Record<string, string> = {};
  if (conn.base_url.trim()) body.base_url = conn.base_url.trim().replace(/\/$/, "");
  if (conn.email.trim()) body.email = conn.email.trim();
  if (conn.api_token.trim()) body.api_token = conn.api_token.trim();
  if (conn.project_key.trim()) body.project_key = conn.project_key.trim();
  if (conn.board_id.trim()) body.board_id = conn.board_id.trim();
  return body;
}

export function useJiraConnection() {
  const [connection, setConnection] = useState<JiraConnection>(emptyConnection);
  const [serverConfig, setServerConfig] = useState<ServerJiraConfig | null>(null);
  const [connectedAs, setConnectedAs] = useState<string | null>(null);
  const [boards, setBoards] = useState<JiraBoardInfo[]>([]);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setConnection(loadSavedConnection());
    axios
      .get<ServerJiraConfig>(`${API}/jira/config`)
      .then((res) => {
        setServerConfig(res.data);
        setConnection((prev) => {
          const next = { ...prev };
          if (!next.base_url && res.data.base_url) next.base_url = res.data.base_url;
          if (!next.email && res.data.email) next.email = res.data.email;
          if (!next.project_key && res.data.project_key)
            next.project_key = res.data.project_key;
          if (!next.board_id && res.data.board_id) next.board_id = res.data.board_id;
          return next;
        });
      })
      .catch(() => setServerConfig(null));
  }, []);

  const updateField = useCallback(
    <K extends keyof JiraConnection>(key: K, value: JiraConnection[K]) => {
      setConnection((prev) => {
        const next = { ...prev, [key]: value };
        saveConnection(next);
        return next;
      });
    },
    []
  );

  const testConnection = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      const res = await axios.post(`${API}/jira/health`, connectionPayload(connection));
      setConnectedAs(res.data.display_name || res.data.email || "Connected");
      const boardsRes = await axios.post(`${API}/jira/boards`, connectionPayload(connection));
      setBoards(boardsRes.data.boards || []);
      return true;
    } catch (err: any) {
      setConnectedAs(null);
      setBoards([]);
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Could not connect to Jira"
      );
      return false;
    } finally {
      setChecking(false);
    }
  }, [connection]);

  return {
    connection,
    setConnection,
    updateField,
    serverConfig,
    connectedAs,
    boards,
    checking,
    error,
    setError,
    testConnection,
    connectionPayload: () => connectionPayload(connection)
  };
}
