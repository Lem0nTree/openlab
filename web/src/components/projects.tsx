"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, idempotencyHeaders, type Location, type Project, type ProjectDetail, type Thing } from "@/lib/api";
import { Shell } from "./shell";

export function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [things, setThings] = useState<Thing[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const [nextProjects, nextThings, nextLocations] = await Promise.all([
        api<Project[]>("/projects"), api<Thing[]>("/things"), api<Location[]>("/locations"),
      ]);
      setProjects(nextProjects); setThings(nextThings); setLocations(nextLocations);
      if (detail) setDetail(await api<ProjectDetail>(`/projects/${detail.id}`));
    } catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { load(); }, []);
  async function select(id: string) { try { setDetail(await api<ProjectDetail>(`/projects/${id}`)); } catch (e) { setError((e as Error).message); } }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    try { const project = await api<Project>("/projects", { method: "POST", body: JSON.stringify({ name: data.get("name"), description: data.get("description") || null }) }); event.currentTarget.reset(); await load(); await select(project.id); } catch (e) { setError((e as Error).message); }
  }
  async function addRequirement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!detail) return; const data = new FormData(event.currentTarget);
    try { await api(`/projects/${detail.id}/requirements`, { method: "POST", body: JSON.stringify({ name: data.get("name"), quantity: Number(data.get("quantity")), priority: data.get("priority"), constraints: {} }) }); event.currentTarget.reset(); await select(detail.id); } catch (e) { setError((e as Error).message); }
  }
  async function allocate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!detail) return; const data = new FormData(event.currentTarget);
    try { await api(`/projects/${detail.id}/allocations`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify({ thing_id: data.get("thing_id"), location_id: data.get("location_id"), quantity: Number(data.get("quantity")), state: data.get("state") }) }); event.currentTarget.reset(); await select(detail.id); } catch (e) { setError((e as Error).message); }
  }
  return <Shell title="Projects & BUILD"><p>Define a BOM, reserve known stock, then move components into use with a traceable recovery path.</p>{error && <p className="error">{error}</p>}<div className="split"><section><h2>Projects</h2><form className="review" onSubmit={create}><input name="name" placeholder="Project name" required /><input name="description" placeholder="Short purpose (optional)" /><button>Create project</button></form><div className="list">{projects.map((project) => <article key={project.id}><button className="link-button" onClick={() => select(project.id)}>{project.name}</button><span className="status">{project.status}</span><p>{project.description}</p></article>)}</div></section>{detail && <section><h2>{detail.name}</h2><h3>BOM requirements</h3><ul>{detail.requirements.map((item) => <li key={item.id}>{item.quantity} × {item.name} <span className="status">{item.priority}</span></li>)}</ul><form className="review" onSubmit={addRequirement}><input name="name" placeholder="Part or requirement" required /><input name="quantity" type="number" min="0.000001" step="any" defaultValue="1" required /><select name="priority" defaultValue="required"><option value="required">Required</option><option value="recommended">Recommended</option><option value="optional">Optional</option></select><button>Add requirement</button></form><h3>Allocations</h3><ul>{detail.allocations.map((item) => <li key={item.id}>{item.quantity} × {things.find((thing) => thing.id === item.thing_id)?.name ?? item.thing_id} <span className="status">{item.state}</span></li>)}</ul><form className="review" onSubmit={allocate}><select name="thing_id" required defaultValue=""><option value="" disabled>Choose Thing</option>{things.map((thing) => <option key={thing.id} value={thing.id}>{thing.name}</option>)}</select><select name="location_id" required defaultValue=""><option value="" disabled>Source location</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select><input name="quantity" type="number" min="0.000001" step="any" defaultValue="1" required /><select name="state" defaultValue="reserved"><option value="reserved">Reserve</option><option value="in_use">Move into use</option><option value="recoverable">Recoverable</option></select><button>Allocate stock</button></form></section>}</div></Shell>;
}
