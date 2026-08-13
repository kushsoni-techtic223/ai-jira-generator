export const EMAIL_RECIPIENTS_KEY = "daily-email-recipients-v1";
const LEGACY_EMAIL_RECIPIENTS_KEY = "github-daily-email-recipients-v1";

export type EmailRecipientsPrefs = {
  to: string;
  cc: string;
};

export function loadEmailRecipients(): EmailRecipientsPrefs {
  try {
    const raw =
      localStorage.getItem(EMAIL_RECIPIENTS_KEY) ||
      localStorage.getItem(LEGACY_EMAIL_RECIPIENTS_KEY);
    if (!raw) return { to: "", cc: "" };
    const parsed = JSON.parse(raw) as Partial<EmailRecipientsPrefs>;
    return {
      to: (parsed.to || "").trim(),
      cc: (parsed.cc || "").trim(),
    };
  } catch {
    return { to: "", cc: "" };
  }
}

export function saveEmailRecipients(prefs: EmailRecipientsPrefs) {
  localStorage.setItem(
    EMAIL_RECIPIENTS_KEY,
    JSON.stringify({
      to: prefs.to.trim(),
      cc: prefs.cc.trim(),
    })
  );
}

export function parseEmailList(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function emailListPayload(to: string, cc: string) {
  const toList = parseEmailList(to);
  const ccList = parseEmailList(cc);
  return {
    ...(toList.length ? { to: toList } : {}),
    ...(ccList.length ? { cc: ccList } : {}),
  };
}
