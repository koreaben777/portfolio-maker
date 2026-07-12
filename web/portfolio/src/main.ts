import "./styles.css";
import portfolioData from "./generated/portfolio-data";

type Evidence = {
  id: number;
  kind: string;
  activity_type?: string;
  title?: string;
  author?: string;
  state?: string;
  created_at?: string;
  url?: string | null;
  label?: string;
};

type Claim = {
  id: number;
  text: string;
  evidence: Evidence[];
};

type Project = {
  id: string;
  name: string;
  repository?: string | null;
  claims: Claim[];
  timeline: Array<{
    evidence_id: number;
    activity_type: string;
    title: string;
    created_at: string;
    url?: string | null;
  }>;
};

type PortfolioManifest = {
  version: number;
  generated_at: string;
  profile: Record<string, string>;
  projects: Project[];
  skills: string[];
  links: Array<{ label: string; url: string }>;
};

const data = portfolioData as unknown as PortfolioManifest;
const app = document.querySelector<HTMLElement>("#app");
if (!app) {
  throw new Error("Portfolio mount point is missing");
}
const mount = app;

let activeProjectId = "all";
let selectedProjectId: string | null = null;

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function render(): void {
  mount.replaceChildren(renderPage());
}

function renderPage(): HTMLElement {
  const page = element("div", "page-shell");
  page.append(renderMasthead(), renderWorkSection(), renderFooter());
  return page;
}

function renderMasthead(): HTMLElement {
  const header = element("header", "masthead");
  const eyebrow = element("p", "eyebrow", "PUBLIC RECORD / EVIDENCE FIRST");
  const title = element("h1", undefined, data.profile.display_name || "Evidence-led portfolio");
  const intro = element(
    "p",
    "intro-copy",
    data.profile.summary || "A considered view of selected work, with the source trail kept close to every public claim.",
  );
  const principle = element("div", "principle");
  principle.append(
    element("p", "eyebrow", "WORKING PRINCIPLE"),
    element("p", "principle-copy", "Make the trail legible."),
  );
  header.append(eyebrow, title, intro, principle);
  return header;
}

function renderWorkSection(): HTMLElement {
  const section = element("section", "work-section");
  const heading = element("div", "section-heading");
  heading.append(
    element("p", "eyebrow", "SELECTED WORK / " + String(data.projects.length).padStart(2, "0")),
    element("h2", undefined, "Projects with a visible source trail."),
  );
  const filters = renderFilters();
  heading.append(filters);
  section.append(heading);

  const body = element("div", "work-layout");
  const projectList = element("div", "project-list");
  const visibleProjects = data.projects.filter(
    (project) => activeProjectId === "all" || project.id === activeProjectId,
  );
  if (visibleProjects.length === 0) {
    projectList.append(
      element(
        "div",
        "empty-state",
        "No public-safe projects are available in this manifest yet. Build or approve evidence before publishing.",
      ),
    );
  } else {
    visibleProjects.forEach((project, index) => {
      projectList.append(renderProjectRow(project, index));
    });
  }
  body.append(projectList, renderDetailPanel(visibleProjects));
  section.append(body);
  return section;
}

function renderFilters(): HTMLElement {
  const nav = element("nav", "filters");
  nav.setAttribute("aria-label", "Project filters");
  const options = [{ id: "all", label: "All work" }, ...data.projects.map((project) => ({ id: project.id, label: project.name }))];
  options.forEach((option) => {
    const button = element("button", "filter-button", option.label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(activeProjectId === option.id));
    button.addEventListener("click", () => {
      activeProjectId = option.id;
      if (option.id !== "all") selectedProjectId = option.id;
      render();
    });
    nav.append(button);
  });
  return nav;
}

function renderProjectRow(project: Project, index: number): HTMLElement {
  const button = element("button", "project-row");
  button.type = "button";
  button.setAttribute("aria-pressed", String(selectedProjectId === project.id));
  button.setAttribute("aria-label", `View details for ${project.name}`);
  button.addEventListener("click", () => {
    selectedProjectId = project.id;
    render();
  });
  button.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const rows = Array.from(document.querySelectorAll<HTMLButtonElement>(".project-row"));
    const nextIndex = event.key === "ArrowDown" ? index + 1 : index - 1;
    rows[Math.max(0, Math.min(rows.length - 1, nextIndex))]?.focus();
  });
  const indexLabel = element("span", "project-index", String(index + 1).padStart(2, "0"));
  const content = element("span", "project-content");
  content.append(
    element("span", "project-kicker", project.repository ? "PUBLIC REPOSITORY" : "PUBLIC SOURCE"),
    element("strong", "project-name", project.name),
    element("span", "project-summary", `${project.claims.length} verified claim${project.claims.length === 1 ? "" : "s"} / ${project.timeline.length} timeline record${project.timeline.length === 1 ? "" : "s"}`),
  );
  const marker = element("span", "project-marker", "View");
  button.append(indexLabel, content, marker);
  return button;
}

function renderDetailPanel(visibleProjects: Project[]): HTMLElement {
  const panel = element("aside", "detail-panel");
  panel.setAttribute("aria-live", "polite");
  const selected = visibleProjects.find((project) => project.id === selectedProjectId) || visibleProjects[0];
  if (!selected) {
    panel.append(
      element("p", "eyebrow", "EVIDENCE DETAIL"),
      element("h3", undefined, "Nothing selected yet."),
      element("p", "detail-copy", "A public-safe project will appear here after evidence is approved and rendered into the manifest."),
    );
    return panel;
  }
  selectedProjectId = selected.id;
  panel.append(
    element("p", "eyebrow", "EVIDENCE DETAIL"),
    element("h3", undefined, selected.name),
    element("p", "detail-copy", "Claims stay connected to their provenance. This view contains only the public-safe records in the build-time manifest."),
  );
  const timeline = element("ol", "timeline");
  selected.timeline.forEach((entry) => {
    const item = element("li", "timeline-item");
    item.append(
      element("span", "timeline-date", entry.created_at || "Undated"),
      element("span", "timeline-type", entry.activity_type),
      element("span", "timeline-title", entry.title),
    );
    if (entry.url) item.append(publicLink(entry.url, "Open source"));
    timeline.append(item);
  });
  panel.append(timeline);
  const skills = element("div", "skills-block");
  skills.append(element("p", "eyebrow", "VERIFIED SKILLS"));
  if (data.skills.length === 0) {
    skills.append(element("p", "empty-note", "No verified skill records are present in this manifest."));
  } else {
    const skillList = element("ul", "skill-list");
    data.skills.forEach((skill) => skillList.append(element("li", undefined, skill)));
    skills.append(skillList);
  }
  panel.append(skills);
  return panel;
}

function publicLink(url: string, label: string): HTMLAnchorElement {
  const link = element("a", "public-link", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  return link;
}

function renderFooter(): HTMLElement {
  const footer = element("footer", "footer");
  footer.append(
    element("span", "footer-label", "PORTFOLIO MAKER / PUBLIC-SAFE MANIFEST"),
    element("span", "footer-note", data.generated_at ? `Generated ${data.generated_at}` : "No generated manifest yet"),
  );
  return footer;
}

render();
