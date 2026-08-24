"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { api, type Project } from "@/lib/api";
import { LabIcon } from "./lab-icon";
import { Shell } from "./shell";

const DATE_FORMAT = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", year: "numeric" });
const PAST_STATUSES = new Set(["completed", "archived", "cancelled"]);

type BuildGroup = { key: "active" | "pending" | "past"; label: string; note: string; projects: Project[] };

function groupProjects(projects: Project[]): BuildGroup[] {
  const groups: BuildGroup[] = [
    { key: "active", label: "Active builds", note: "Currently on the bench", projects: [] },
    { key: "pending", label: "Pending builds", note: "Ideas and plans waiting to start", projects: [] },
    { key: "past", label: "Past builds", note: "Completed and archived work", projects: [] },
  ];
  const byKey = new Map(groups.map((group) => [group.key, group]));
  for (const project of projects) {
    const key = PAST_STATUSES.has(project.status)
      ? "past"
      : project.status === "pending"
        ? "pending"
        : "active";
    byKey.get(key)?.projects.push(project);
  }
  return groups;
}

export function Projects() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api<Project[]>("/projects")
      .then((value) => { if (!cancelled) setProjects(value); })
      .catch((nextError: Error) => { if (!cancelled) setError(nextError.message); });
    return () => { cancelled = true; };
  }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setCreating(true);
    setError("");
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: data.get("name"), description: data.get("description") || null }),
      });
      router.push(`/projects/${project.id}`);
    } catch (nextError) {
      setError((nextError as Error).message);
      setCreating(false);
    }
  }

  const groups = groupProjects(projects);
  const activeCount = groups[0].projects.length;
  const pendingCount = groups[1].projects.length;
  const pastCount = groups[2].projects.length;

  return <Shell title="Builds">
    <section className="build-overview build-index-hero">
      <div><p className="eyebrow">PROJECT CONTROL</p><h2>Every build, from first idea to tested wiring.</h2><p>Track what is waiting, what is on the bench, and what you already completed. Open a build for its selected solution, pinouts, wiring, instructions, cost, and checks.</p></div>
      <div className="build-stats"><span><b>{activeCount}</b><small>ACTIVE</small></span><i/><span><b>{pendingCount}</b><small>PENDING</small></span><i/><span><b>{pastCount}</b><small>PAST</small></span></div>
    </section>
    {error && <p className="error build-error">{error}</p>}

    <section className="build-create-card">
      <div><p className="eyebrow">NEW BUILD</p><h2>Start with the outcome.</h2><p>Describe what the build should do. OpenLab will match it against your actual inventory.</p></div>
      <form className="project-create-form project-create-inline" onSubmit={create}>
        <label><span>Build name</span><input name="name" placeholder="e.g. Plant moisture monitor" required /></label>
        <label><span>Purpose</span><input name="description" placeholder="What should it do?" /></label>
        <button disabled={creating}>{creating ? "Creating…" : "Create build"}<LabIcon name="arrow"/></button>
      </form>
    </section>

    <div className="build-groups">
      {groups.map((group) => <section className={`build-group is-${group.key}`} key={group.key}>
        <div className="build-group-head"><div><p className="eyebrow">{group.key.toUpperCase()}</p><h2>{group.label}</h2><p>{group.note}</p></div><strong>{group.projects.length}</strong></div>
        <div className="build-card-grid">
          {group.projects.map((project) => <Link className="build-card" href={`/projects/${project.id}`} key={project.id}>
            <span className="build-card-icon"><LabIcon name={project.status === "completed" ? "check" : "folder"}/></span>
            <span className="build-card-main"><small>{project.status.replaceAll("_", " ")}</small><strong>{project.name}</strong><p>{project.description || "No purpose recorded yet."}</p></span>
            <span className="build-card-meta"><small>UPDATED</small><time dateTime={project.updated_at}>{DATE_FORMAT.format(new Date(project.updated_at))}</time><LabIcon name="arrow"/></span>
          </Link>)}
          {group.projects.length === 0 && <div className="build-group-empty"><LabIcon name="folder"/><p>No {group.key} builds.</p></div>}
        </div>
      </section>)}
    </div>
  </Shell>;
}
