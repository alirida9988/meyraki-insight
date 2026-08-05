# Meyraki Insight — Source of Truth

> Single authoritative product definition, synthesized on 2026-08-05 from every Meyraki
> document in `~/Downloads` (notes, 6 decks, pitch script, dev docs, API spec, MVP
> checklist, architecture diagram, UI mockups, sample data, logo). Where documents from
> different timelines conflicted, the resolution and its rationale are recorded in §8.
> This file wins over any individual source document.

---

## 1. What Meyraki Insight is

**An AI-powered spatial-intelligence SaaS that turns floorplans and behavioral data into
revenue.** It analyzes interior spaces the way a consultant would — but instantly and
visually — producing optimized layouts, guest-flow heatmaps, design moodboards, and
business-impact metrics (ROI per sqm, flow efficiency) in a branded, client-ready report.

- **Parent brand:** Méyraki — architecture, interior design, event planning studio.
- **Team:** Israa Sammoura (CEO & Founder), Jad El Khatib (Head of Innovation & Marketing), in-house design + IT team.
- **Traction:** Startup Den finalist at Future Hospitality Summit; pilot with boutique
  hotel "Cleo Urban Stay" (+15% guest-flow efficiency, rooftop monetized as coworking
  café); client reference: Ministry of Minerals, Saudi Arabia.
- **Positioning:** "We are not a drawing tool — we are a decision-making tool."
  AutoCAD/Revit/SketchUp render; Meyraki predicts, optimizes, and prices space.

## 2. Who it's for

| Persona | Job to be done | Pays via |
|---|---|---|
| Boutique hotel owner / operator | Fix congestion, monetize dead zones, justify reno spend | Pay-per-report |
| Interior design / architecture studio | Win pitches with data, iterate layouts faster | SaaS subscription |
| Real-estate developer / hotel group | Standardize spatial ROI analysis across properties | White-label / enterprise |

Launch vertical: **hospitality** (boutique hotels, cafés, restaurants, coworking).
Adjacent: offices, clinics, banks, galleries, event spaces.
Region focus: **MENA/GCC first** (KSA hospitality boom, no direct regional competitor,
Arabic support is a differentiator), then EU/US.

## 3. The user journey (canonical flow)

1. **Upload** — floorplan (PNG/JPG/PDF; DWG in a later phase) + optional footfall CSV
   (`zone_name, timestamp, traffic_count`) or a preset dataset; space type (hotel, café,
   office…).
2. **Objectives** — pick goal(s): maximize guest flow · seating efficiency · revenue per
   sqm · ambiance. Optional natural-language brief ("more sunlight and a coworking feel
   in the lounge").
3. **AI pipeline** (agent orchestration, see `01-ARCHITECTURE.md`):
   - Zone detection — rooms/zones recognized and labeled from the plan
   - Flow/heatmap simulation — from footfall data or synthetic simulation
   - Layout optimization — concrete, justified change suggestions
   - Moodboard generation — palette, materials, furniture style, rendered previews
   - Business metrics — ROI per sqm, Guest Flow Efficiency Score, forecast uplift
4. **Results dashboard** — heatmap overlay on plan, A/B scenario comparison
   ("Scenario A: open lounge vs Scenario B: segmented seating" with projected outcomes),
   moodboard, metrics.
5. **Export** — branded PDF Insight Report (client name/logo, layouts, heatmaps,
   moodboards, recommendations, business case). Later: SketchUp/AutoCAD/Revit/Figma export.

## 4. Feature inventory (deduplicated across all documents)

**MVP (Phase 1)**
- Floorplan upload (PNG/JPG/PDF) + footfall CSV upload + presets
- Objective selection (flow / seating / revenue / ambiance) + NL brief
- AI zone detection with semantic labels (reception, corridor, lounge…)
- Heatmap generation (from data or simulation)
- Layout recommendations with plain-language justification
- AI moodboard (palette + materials + style + generated images)
- Business impact metrics: ROI per sqm, flow efficiency %, occupancy flow score
- Branded PDF Insight Report export
- Project workspace: per-client projects, history

**Phase 2 — Enhanced intelligence**
- A/B scenario simulation with predicted outcomes ("+15% flow", "+20% seating efficiency")
- Impact forecasting ("changing lobby shape adds 12% to guest flow, −3 min idle wait")
- Interactive AI co-designer chat ("I want more sunlight…" → live suggestions)
- DWG ingestion; SketchUp/AutoCAD plugin export; Figma boards export
- Smart zoning & behavior mapping: emotional zones, stress points, relaxation zones
- Layout Sentiment Score (predicted guest satisfaction from ambiance + structure)

**Phase 3 — SaaS platform scale**
- Client login portal (upload/view/adjust, download reports)
- ROI tracking engine over time; multi-property optimization
- Interactive layout builder (drag-and-drop with live AI feedback)
- Report automation; white-label deployments
- IoT/sensor ingestion (thermal sensors, cameras) to close the design↔behavior loop
- Plugin marketplace ambitions; urban-scale/gov dashboards (long-term vision)

## 5. Brand & UI direction

**Superseded (founder directive 2026-08-05):** the dark/violet Figma-style mockups are
historical — kept for *screen flow* only (Upload Your Project → Define Objectives →
Insight Output → AI-Generated Design). The authoritative visual identity is
`02-DESIGN-SYSTEM.md`: modern minimalist, high-class — gallery-white "drawing sheet"
surfaces, warm ink, hairline rules, deep viridian accent, thermal ramp reserved for
data; Instrument Serif / Instrument Sans / IBM Plex Mono; dimension-line annotation as
the signature device. Arabic/RTL remains first-class from day one.

## 6. Business model

- SaaS subscription for design studios
- Pay-per-report for developers/one-off clients
- White-label/enterprise for hotel groups, REITs, ministries
- The Ask (investor deck): pilot partners, technical mentorship, strategic funding

## 7. Competitive frame (from SWOT decks)

Spacemaker/Forma (urban, not hospitality) · TestFit (RE feasibility, no design visuals) ·
Cove.tool (sustainability) · Morpholio (moodboards, not data-driven) · Archistar
(compliance/generative planning, not interiors). Regional (GCC/MENA): no direct AI
interior/hospitality competitor identified. Threats: BIM suites adding similar features.
Moat: hospitality specificity + design-to-revenue link + visual-first UX + regional/Arabic
localization. (Live competitor re-verification: `04-REUSE-MAP.md`.)

## 8. Conflict resolutions (different-timeline documents)

| Topic | Sources say | Resolution | Why |
|---|---|---|---|
| Frontend | Flutter Web (tech deck, dev docs) vs React (+Three.js) (notes, dev docs alt) | **React (Next.js) + Three.js** | Web-first SaaS; Three.js/canvas ecosystem, PDF/report tooling, and hiring pool are far stronger in React; Flutter Web is weak at SEO, text rendering, and third-party JS libs. Mobile app, if ever needed, can come later. |
| AI engine | OpenCV + scikit-learn (2024-era docs) vs "strong AI agent orchestration" (founder, 2026) | **LLM-vision agent pipeline first, classical CV as deterministic tools inside it** | Frontier multimodal models now read floorplans semantically (zones, labels, context) far better than hand-built CV; OpenCV remains for pixel-level ops (masks, overlays, heatmap rendering) as agent-callable tools. Best of both. |
| API surface | GET for generation endpoints (spec sheet) | **POST + async job pattern** | Generation is long-running and side-effectful; GET is wrong HTTP semantics and times out. Job-based API with status polling/webhooks. Endpoint names kept. |
| Phase dates | 2024/2025/2026 phase years in decks | **Relative phases (1/2/3), no calendar years** | Dates were aspirational and from different timelines; phases stay valid. |
| DWG support at MVP | "JPG, PDF, DWG" everywhere | **MVP: PNG/JPG/PDF. DWG/IFC: Phase 2** | DWG is a proprietary format; parsing it well is a project by itself. PDF/image covers boutique-hospitality reality (most clients have PDFs). |
| Product name | "Mirakel" (spoken), Meyraki Insight (all documents) | **Meyraki Insight** | Every artifact, logo, and deck uses Meyraki Insight. |

## 9. Source documents indexed

| Document | Type | What was taken |
|---|---|---|
| `meyraki-notes.txt` | Notes (the "Mirakel Notes") | Vision, process flow, novel features, MVP goals, tech ideas |
| `Meyraki_Insight_Developer_Documentation.pdf` + `README_MeyrakiInsight_DevDocs.md` | Dev docs | Components, inputs, endpoints, outputs, stack (superseded per §8) |
| `Meyraki_Insight_API_Spec_Sheet.pdf` | API spec | Endpoint inventory (semantics fixed per §8) |
| `Meyraki_Insight_System_Architecture_Diagram.png` | Diagram | Component graph: UI → API → AI Engine → Insight Gen → Moodboard → Report → DB/S3 |
| `Meyraki_Insight_Developer_Technical_Deck.pptx` | Deck | AI engine logic, use cases, next steps |
| `MeyrakiInsight_InvestorPartner_PitchDeck_Enhanced.pptx` + StartupDen deck | Decks | Positioning, case study, roadmap, team, partners, vision evolution |
| `Meyraki_Insight_Pitch_Script_and_Demo_Flow.docx` | Script | Narrative, Q&A (accuracy, differentiation, monetization) |
| `Meyraki_Insight_Competitor_SWOT_Pack.pptx` + `..._Updated_With_Region.pptx` | Decks | Competitive landscape, SWOT, regional strategy |
| `Meyraki_Insight_Roadmap_Slide.pptx` | Deck | Phase 1/2/3 scope |
| `MeyrakiInsight_MVP_Simulation_Checklist.pdf` | Checklist | Demo asset list, screen list |
| `A_digital_screenshot_displays_a_Figma_interactive_.png` | UI mock | Screen flow + visual language (§5) |
| `A_2D_architectural_floor_plan_of_an_indoor_space,_.png` | Sample | Heatmap-on-plan reference output |
| `MeyrakiInsight_Sample_FootfallData.csv` | Data | Footfall schema: `zone_name, timestamp, traffic_count` |
| `meyraki-logo.jpeg` | Brand | Logotype, palette, parent-brand services |
