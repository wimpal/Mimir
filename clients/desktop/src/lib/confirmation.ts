/**
 * Client-side heuristic for M3 write confirmation prompts.
 * Port of clients/mobile/.../ConfirmationDetector.kt
 */

const confirmQuestionPattern =
  /(shall i|should i|do you want|confirm|go ahead|zal ik|wil je dat ik|mag ik|bevestig|klopt dat|is dat goed|voeg ik|toevoegen\?|save\s+(this|the)\s+recipe|bewaar\s+(dit|het)\s+recept)/i;

const recipeSavePatterns = [
  /\bsave\b.*\brecipe\b/i,
  /\brecipe\b.*\bsave\b/i,
  /\badd\b.*\brecipe\b/i,
  /\bimport\b.*\brecipe\b/i,
  /\bbewaar\b.*\brecept\b/i,
  /\bvoeg\b.*\brecept\b.*\btoe\b/i,
  /\brecept\s+opslaan\b/i,
  /\bsla\b.*\brecept\b.*\bop\b/i,
  /\bimporteer\b.*\brecept\b/i,
];

const mutationPatterns = [
  /\badd\b.*\b(to\s+the\s+)?(shopping\s+)?list\b/i,
  /\bvoeg\b.*\b(toe|toe aan)\b/i,
  /\bwe\s+spent\b/i,
  /\b€\s*\d/,
  /\b(betaald|uitgegeven|gekocht)\b/i,
  /\brecord\b.*\b(expense|transaction|uitgave)\b/i,
];

const readOnlyPatterns = [
  /\bwhat('s| is)\s+(on\s+the\s+)?(shopping\s+)?list\b/i,
  /\bwhat('s| is)\s+low\b/i,
  /\bhow\s+much\s+(did\s+we\s+)?spend/i,
  /\bwhat\s+can\s+(i|we)\s+cook\b/i,
  /\brecept\s+met\b/i,
  /\bwat\s+kunnen\s+we\s+koken\b/i,
];

/** Tools that do not mean a write already happened. */
const readOnlyTools = new Set([
  "web.fetch",
  "homebase.recipes.search",
  "homebase.recipes.get",
  "homebase.inventory.list",
  "homebase.shopping_list.list",
  "homebase.tasks.list",
  "homebase.lights.list",
  "get_weather",
  "get_calendar",
  "get_server_time",
  // Staged add returns awaiting_confirmation — Confirm buttons must still show.
  "homebase.recipes.add",
]);

export function userMessageRequestsWrite(text: string): boolean {
  const normalized = text.trim();
  if (!normalized) return false;
  if (recipeSavePatterns.some((r) => r.test(normalized))) return true;
  if (readOnlyPatterns.some((r) => r.test(normalized))) return false;
  return mutationPatterns.some((r) => r.test(normalized));
}

export function shouldShowWriteConfirm(
  assistantText: string,
  toolsUsed: string[],
  priorUserMessage: string | null | undefined,
): boolean {
  const writeToolsUsed = toolsUsed.filter((t) => !readOnlyTools.has(t));
  if (writeToolsUsed.length > 0) return false;
  const text = assistantText.trim();
  if (!text) return false;
  if (!confirmQuestionPattern.test(text)) return false;
  if (priorUserMessage != null && userMessageRequestsWrite(priorUserMessage)) {
    return true;
  }
  return text.includes("?") && confirmQuestionPattern.test(text);
}

function looksDutch(text: string): boolean {
  const lower = text.toLowerCase();
  const dutchHints = [
    "zal ik",
    "wil je",
    "mag ik",
    "bevestig",
    "boodschappen",
    "uitgave",
    "meneer",
    "recept",
    "bewaar",
  ];
  return dutchHints.some((h) => lower.includes(h));
}

export function confirmReply(assistantText: string): string {
  return looksDutch(assistantText) ? "ja" : "yes";
}

export function cancelReply(assistantText: string): string {
  return looksDutch(assistantText) ? "nee" : "no";
}
