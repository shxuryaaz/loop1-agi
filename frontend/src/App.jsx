import { useState } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

// In production, set VITE_API_URL (e.g. https://your-app.onrender.com) in the
// hosting env. Falls back to the local backend during development.
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_URL = `${API_BASE.replace(/\/$/, "")}/api/process`;

/**
 * Sanitize a sprint label into a safe filename slug:
 * lowercase, non-alphanumeric -> "_", collapse repeats, strip leading/trailing "_".
 */
function sanitizeLabel(label) {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export default function App() {
  const [file, setFile] = useState(null);
  const [sprintLabel, setSprintLabel] = useState("");
  const [teamMembers, setTeamMembers] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null); // { markdown, meeting_summary, people_count }

  function handleFileChange(event) {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResult(null);

    if (!file) {
      setError("Please choose a .txt transcript file.");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setError("The file must be a .txt transcript.");
      return;
    }
    if (!sprintLabel.trim()) {
      setError("Please enter a sprint label.");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("sprint_label", sprintLabel.trim());
    if (teamMembers.trim()) {
      formData.append("team_members", teamMembers.trim());
    }

    setLoading(true);
    try {
      const response = await fetch(API_URL, {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      if (!response.ok) {
        setError(data?.error || `Request failed (${response.status}).`);
      } else {
        setResult(data);
      }
    } catch (err) {
      setError(
        "Could not reach the backend. Make sure it is running on http://localhost:8000."
      );
    } finally {
      setLoading(false);
    }
  }

  function handleDownload() {
    if (!result?.markdown) return;
    const slug = sanitizeLabel(sprintLabel) || "sprint";
    const blob = new Blob([result.markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `sprint_goals_${slug}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Agilow — Sprint Goals Generator</h1>
        <p className="subtitle">
          Upload a meeting transcript and generate a formatted sprint goals
          document.
        </p>
      </header>

      <form className="card form" onSubmit={handleSubmit}>
        <label className="field">
          <span className="field-label">Transcript file (.txt)</span>
          <input type="file" accept=".txt,text/plain" onChange={handleFileChange} />
          {file && <span className="file-name">Selected: {file.name}</span>}
        </label>

        <label className="field">
          <span className="field-label">Sprint label</span>
          <input
            type="text"
            placeholder="June 6th - June 13th"
            value={sprintLabel}
            onChange={(e) => setSprintLabel(e.target.value)}
          />
        </label>

        <label className="field">
          <span className="field-label">Team members (comma-separated, optional)</span>
          <input
            type="text"
            placeholder="Shiv, Antonio, Keith"
            value={teamMembers}
            onChange={(e) => setTeamMembers(e.target.value)}
          />
        </label>

        <button type="submit" className="primary-button" disabled={loading}>
          {loading ? (
            <span className="loading">
              <span className="spinner" aria-hidden="true" />
              Processing transcript…
            </span>
          ) : (
            "Generate"
          )}
        </button>
      </form>

      {error && (
        <div className="card error-box" role="alert">
          <strong>Error:</strong> {error}
        </div>
      )}

      {result && (
        <div className="card result">
          <div className="result-header">
            <div>
              <h2>Sprint Goals</h2>
              <p className="result-meta">
                {result.people_count}{" "}
                {result.people_count === 1 ? "person" : "people"} with
                commitments
              </p>
            </div>
            <button className="secondary-button" onClick={handleDownload}>
              Download .md
            </button>
          </div>

          {result.meeting_summary && (
            <p className="summary">{result.meeting_summary}</p>
          )}

          <div className="markdown-body">
            <ReactMarkdown>{result.markdown}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
