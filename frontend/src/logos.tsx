import claude from "@lobehub/icons-static-svg/icons/claude-color.svg?raw";
import gemini from "@lobehub/icons-static-svg/icons/gemini-color.svg?raw";
import grok from "@lobehub/icons-static-svg/icons/grok.svg?raw";
import groq from "@lobehub/icons-static-svg/icons/groq.svg?raw";
import ollama from "@lobehub/icons-static-svg/icons/ollama.svg?raw";
import openai from "@lobehub/icons-static-svg/icons/openai.svg?raw";

// Provider logos: LobeHub icons , inlined as SVG so they stay sharp at every size. MIT.
const LOGOS: Record<string, string> = { anthropic: claude, gemini, openai, xai: grok, groq, ollama };

export function ProviderLogo({ id, size = "md" }: { id: string; size?: "sm" | "md" }) {
  const svg = LOGOS[id];
  if (!svg) return null;
  return <span className={`logo ${size === "sm" ? "sm" : ""}`} aria-hidden dangerouslySetInnerHTML={{ __html: svg }} />;
}
