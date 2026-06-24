"use client";

// /world — playable overworld prototype. The learner walks a top-down map and
// approaches a topic "stage"; facing it + pressing A opens a Pokémon-style
// encounter dialog that drops into the existing learning loop (/play?topic=).
// Self-contained: it does NOT touch /play or the rest of the app.

import { useCallback, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import Overworld from "@/components/retro/Overworld";
import { createSampleOverworld, type MapNode } from "@/lib/overworld";
import "../retro.css";

export default function WorldPage() {
  const router = useRouter();
  const [map] = useState(() => createSampleOverworld());
  const [encounter, setEncounter] = useState<MapNode | null>(null);
  const onEnterNode = useCallback((n: MapNode) => setEncounter(n), []);

  function begin(n: MapNode) {
    setEncounter(null);
    router.push(`/play?topic=${encodeURIComponent(n.topic)}`);
  }

  return (
    <main
      className="pixel-quest"
      style={{
        minHeight: "100vh",
        background: "var(--pq-bg)",
        color: "var(--pq-text)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "20px 12px 40px",
        gap: 16,
      }}
    >
      <header style={{ width: "100%", maxWidth: 520, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 style={{ fontFamily: "var(--pq-font-pixel)", fontSize: 14, lineHeight: 1.4 }}>CURIO · OVERWORLD</h1>
        <button onClick={() => router.push("/")} style={chipStyle}>← Home</button>
      </header>

      <Overworld map={map} onEnterNode={onEnterNode} />

      {encounter && <EncounterDialog node={encounter} onBegin={() => begin(encounter)} onClose={() => setEncounter(null)} />}
    </main>
  );
}

function EncounterDialog({
  node,
  onBegin,
  onClose,
}: {
  node: MapNode;
  onBegin: () => void;
  onClose: () => void;
}) {
  const locked = node.state === "locked";
  const goal = node.state === "goal";
  const title = locked
    ? `« ${node.label} » is sealed`
    : goal
      ? `The Dragon guards « ${node.label} »`
      : `A wild lesson: « ${node.label} »`;
  const body = locked
    ? "Clear an earlier stage to unlock this path."
    : goal
      ? "Face the final challenge?"
      : "Begin the encounter?";

  return (
    <div style={overlayStyle}>
      <div style={dialogStyle}>
        <p style={{ fontFamily: "var(--pq-font-pixel)", fontSize: 11, lineHeight: 1.5, color: "#1a1426", marginBottom: 10 }}>{title}</p>
        <p style={{ fontSize: 14, color: "#1a1426", marginBottom: 14 }}>{body}</p>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          {!locked && (
            <button onClick={onBegin} style={{ ...dlgBtn, background: goal ? "var(--pq-red)" : "var(--pq-cyan)", color: "#fff" }}>
              {goal ? "Enter battle" : "Begin"}
            </button>
          )}
          <button onClick={onClose} style={{ ...dlgBtn, background: "#fff", color: "#1a1426" }}>
            {locked ? "OK" : "Walk away"}
          </button>
        </div>
      </div>
    </div>
  );
}

const chipStyle: CSSProperties = {
  border: "3px solid var(--pq-ink)",
  background: "var(--pq-panel)",
  color: "var(--pq-text)",
  fontFamily: "var(--pq-font-pixel)",
  fontSize: 9,
  padding: "8px 10px",
  cursor: "pointer",
};

const overlayStyle: CSSProperties = {
  position: "fixed",
  left: 0,
  right: 0,
  bottom: 0,
  display: "flex",
  justifyContent: "center",
  padding: 12,
};

const dialogStyle: CSSProperties = {
  width: "100%",
  maxWidth: 520,
  background: "var(--pq-paper)",
  border: "4px solid var(--pq-ink)",
  boxShadow: "6px 6px 0 0 var(--pq-ink)",
  padding: 16,
};

const dlgBtn: CSSProperties = {
  border: "3px solid var(--pq-ink)",
  fontFamily: "var(--pq-font-pixel)",
  fontSize: 10,
  padding: "10px 14px",
  cursor: "pointer",
};
