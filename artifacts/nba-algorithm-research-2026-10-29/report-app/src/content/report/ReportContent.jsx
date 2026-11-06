import React from "react";

import {
  ChartRenderer, DataComponent, DataTable, MetricCard, ReportSection,
  RichNarrative, useDataApp,
} from "../../data-app-public.jsx";

const pct = (value, digits = 1) => value == null ? "N/A" : `${(Number(value) * 100).toFixed(digits)}%`;
const fixed = (value, digits = 4) => value == null ? "N/A" : Number(value).toFixed(digits);

function EvidenceNarrative({ id, queryIds, value, className = "report-analysis" }) {
  const { reviewedRows } = useDataApp();
  return <ReportSection id={id} title={id} queryId={queryIds[0]} queryIds={queryIds}
    sourceRowsByQuery={Object.fromEntries(queryIds.map((queryId) => [queryId, reviewedRows(queryId)]))}
    showHeading={false}>
    <RichNarrative id={`${id}:body`} className={className} value={value} />
  </ReportSection>;
}

function EvidenceTable({ id, title, queryId, rows, columns, description, searchable = true }) {
  return <DataComponent id={id} title={title} queryId={queryId} kind="table"
    sourceRows={rows} displayRows={rows} description={description}>
    <DataTable rows={rows} columns={columns} searchable={searchable}
      compactNumbers={false} label={title} />
  </DataComponent>;
}

export function ReportContent() {
  const { appTitle, setAppTitle, canEdit, mode, reviewedRows, chartOverrides, chartProps } = useDataApp();
  const models = reviewedRows("model_comparison");
  const ratings = reviewedRows("current_ratings");
  const forecasts = reviewedRows("forecast_diagnostics");
  const seasons = reviewedRows("season_metrics");
  const summaryRows = reviewedRows("current_summary");
  const [summary] = summaryRows;
  const currentElo = models.find((row) => row.key === "current_elo");
  const existingCandidate = models.find((row) => row.key === "existing_research_candidate");
  const hybrid = models.find((row) => row.key === "hybrid_candidate");
  const awayForecasts = forecasts.filter((row) => row.selection_side === "AWAY");
  const modelDisplay = models.filter((row) => row.key !== "corrected_elo").map((row) => ({
    ...row, accuracyPct: row.accuracy * 100, homePickPctDisplay: row.homePickPct * 100,
  }));
  const seasonDisplay = seasons.map((row) => ({...row, accuracyPct: row.accuracy * 100}));
  const accuracyChart = chartOverrides["accuracy-chart"] ?? {
    type: "horizontalBar", x: "model", y: "accuracyPct", yLabel: "Accuracy (%)", valueDecimals: 1,
  };
  const brierChart = chartOverrides["brier-chart"] ?? {
    type: "horizontalBar", x: "model", y: "brierScore", yLabel: "Brier score (lower is better)", valueDecimals: 4,
  };
  const homePickChart = chartOverrides["home-pick-chart"] ?? {
    type: "horizontalBar", x: "model", y: "homePickPctDisplay", yLabel: "Home selections (%)", valueDecimals: 1,
  };
  const seasonChart = chartOverrides["season-chart"] ?? {
    type: "line", x: "season", y: "accuracyPct", series: "model", yLabel: "Accuracy (%)", valueDecimals: 1,
  };

  return <article className="report-content" aria-label="NBA algorithm diagnosis report">
    <header className="report-hero">
      <p className="report-kicker">MODEL RESEARCH · AS OF OCTOBER 29, 2026</p>
      <h1 data-data-app-title contentEditable={canEdit && mode === "edit"} suppressContentEditableWarning
        onBlur={canEdit && mode === "edit" ? (event) => setAppTitle(event.currentTarget.textContent.trim() || appTitle) : undefined}
        onKeyDown={canEdit && mode === "edit" ? (event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } } : undefined}>{appTitle}</h1>
      <RichNarrative id="report:intro" className="report-deck"
        value="A leakage-safe diagnosis of the 100-home / 2-away production Elo split, an independent reproduction of prior research, and a governed recommendation for the next NBA game-winner shadow model." />
    </header>

    <EvidenceNarrative id="executive-verdict" queryIds={["current_summary", "model_comparison"]}
      className="report-summary-lead" value={`## Executive verdict

**The current Elo is mathematically correct and current data is reaching it.** The home-heavy output is not a stale-ingestion failure. Every team starts the season at 1500, ratings have only 68 games to separate, and the model adds **65 Elo points** to the home team. At equal ratings that is already a **${pct(summary?.equalRatingHomeProbability, 2)} home probability**.

**An away pick requires an away Elo lead greater than 65 points.** The entire current rating range is only **${fixed(summary?.minimumRating, 2)} to ${fixed(summary?.maximumRating, 2)}**, so only two of 102 matchups cross the threshold.

**The historical baseline is useful but not strongest.** Current Elo reached **${pct(currentElo?.accuracy, 2)}** on 1,231 untouched 2025–26 games, well above the 55.48% always-home baseline. The existing operationally compatible candidate reached **${pct(existingCandidate?.accuracy, 2)}** with better probability quality. A hybrid scored **${pct(hybrid?.accuracy, 2)}**, but its small incremental gain and missing live prior-season state make it exploratory rather than promotion-ready.`} />

    <div className="report-facts" aria-label="Key findings">
      <MetricCard id="metric-current-split" title="Current Elo split" queryId="current_summary" sourceRows={summaryRows}
        value={`${summary?.homeSelections} home / ${summary?.awaySelections} away`}
        comparison={`${summary?.upcomingForecasts} upcoming games`} description="Exact reconstruction of the production forecast population." />
      <MetricCard id="metric-baseline" title="Current Elo test accuracy" queryId="model_comparison" sourceRows={[currentElo]}
        value={pct(currentElo?.accuracy, 2)} comparison={`Brier ${fixed(currentElo?.brierScore, 5)}`}
        description="Untouched 2025–26 season; 1,231 identical games." />
      <MetricCard id="metric-candidate" title="Existing candidate" queryId="model_comparison" sourceRows={[existingCandidate]}
        value={pct(existingCandidate?.accuracy, 2)} comparison={`Brier ${fixed(existingCandidate?.brierScore, 5)}`}
        description="Independently reproduced form/rest logistic shadow candidate." />
      <MetricCard id="metric-hybrid" title="Best numerical test" queryId="model_comparison" sourceRows={[hybrid]}
        value={pct(hybrid?.accuracy, 2)} comparison={`Brier ${fixed(hybrid?.brierScore, 5)}`}
        description="Exploratory hybrid; not operationally complete and not approved." />
    </div>

    <section className="report-section">
      <EvidenceNarrative id="elo-root-cause" queryIds={["current_summary", "current_ratings", "forecast_diagnostics"]}
        value={`## Why the model selects almost every home team

The exact decision boundary is **home Elo + 65 ≥ away Elo**. That makes home the default until an away team is more than 65 rating points stronger. The baseline uses no previous-season carryover, mean regression, margin-of-victory multiplier, rest, form, injuries, lineups, or market data. Its probability conversion and zero-sum K=20 update are correct; the limitation is how little team separation exists early in a reset season.

The two away selections are **DET at BKN** and **POR at UTAH**. Their adjusted home differences are only -2.81 and -0.09 points, corresponding to away probabilities barely above 50%. A balanced split is not an objective, and no parameter was changed merely to create more away picks.`} />
      <EvidenceTable id="away-picks-table" title="The only current away selections" queryId="forecast_diagnostics"
        rows={awayForecasts} searchable={false} description="Away selections occur only when adjusted home rating difference is below zero."
        columns={[
          {field:"kickoff_time",label:"Tipoff (UTC)"},{field:"away_team",label:"Away"},{field:"home_team",label:"Home"},
          {field:"away_rating",label:"Away Elo"},{field:"home_rating",label:"Home Elo"},
          {field:"adjusted_home_rating_difference",label:"Adjusted diff"},{field:"away_win_probability",label:"Away probability",presentation:"percent"},
          {field:"predicted_winner",label:"Pick"},
        ]} />
      <EvidenceTable id="ratings-table" title="All 30 current team Elo ratings" queryId="current_ratings"
        rows={ratings} description="Ratings after the 68 eligible 2026–27 regular-season finals at the frozen production cutoff."
        columns={[
          {field:"team",label:"Team"},{field:"elo_rating",label:"Elo rating"},
          {field:"last_completed_game_id",label:"Last game ID"},{field:"last_completed_game_kickoff",label:"Last update (UTC)"},
        ]} />
    </section>

    <section className="report-section">
      <EvidenceNarrative id="model-comparison-summary" queryIds={["model_comparison", "season_metrics"]}
        value={`## Historical comparison

The frozen evaluation uses **2023–24 for training**, **2024–25 for validation and parameter selection**, and **2025–26 once as an untouched test**. Features are emitted before each game's score is applied. The earlier 68.56% candidate result was reproduced independently with zero probability difference on all 1,231 held-out games.

The hybrid leads the single untouched test season, but the existing candidate remains the safest operational shadow: it is already versioned, artifact-hashed, and uses inputs produced by the daily pipeline. The hybrid depends on a prior-season Elo state that the current live store does not yet preserve. Its incremental Brier improvement over the existing candidate is only 0.00133, so another independent period is warranted before adding that dependency.`} />
      <DataComponent id="accuracy-chart" title="Held-out winner accuracy" queryId="model_comparison" kind="chart"
        chart={accuracyChart} sourceRows={models} displayRows={modelDisplay} description="Untouched 2025–26 test season; identical 1,231-game population.">
        <ChartRenderer spec={accuracyChart} rows={modelDisplay} height={390} {...chartProps("accuracy-chart")} />
      </DataComponent>
      <DataComponent id="brier-chart" title="Held-out probability error" queryId="model_comparison" kind="chart"
        chart={brierChart} sourceRows={models} displayRows={modelDisplay} description="Lower Brier score is better.">
        <ChartRenderer spec={brierChart} rows={modelDisplay} height={390} {...chartProps("brier-chart")} />
      </DataComponent>
      <DataComponent id="home-pick-chart" title="Home selections are a diagnostic, not a target" queryId="model_comparison" kind="chart"
        chart={homePickChart} sourceRows={models} displayRows={modelDisplay} description="Selection balance alone does not establish model quality.">
        <ChartRenderer spec={homePickChart} rows={modelDisplay} height={390} {...chartProps("home-pick-chart")} />
      </DataComponent>
      <EvidenceTable id="model-table" title="Untouched 2025–26 model results" queryId="model_comparison"
        rows={models} searchable={false} description="Lower Brier, log loss, and ECE are better; higher accuracy and AUC are better."
        columns={[
          {field:"model",label:"Model"},{field:"accuracy",label:"Accuracy",presentation:"percent"},
          {field:"brierScore",label:"Brier"},{field:"logLoss",label:"Log loss"},{field:"auc",label:"AUC"},
          {field:"expectedCalibrationError",label:"ECE"},{field:"homePickPct",label:"Home pick %",presentation:"percent"},
          {field:"games",label:"Games"},
        ]} />
      <DataComponent id="season-chart" title="Accuracy across validation and untouched test seasons" queryId="season_metrics" kind="chart"
        chart={seasonChart} sourceRows={seasons} displayRows={seasonDisplay}
        description="2024–25 was used for model selection; only 2025–26 is untouched test evidence.">
        <ChartRenderer spec={seasonChart} rows={seasonDisplay} height={330} {...chartProps("season-chart")} />
      </DataComponent>
    </section>

    <section className="report-section">
      <EvidenceNarrative id="data-boundary" queryIds={["model_comparison", "forecast_diagnostics"]}
        value="## What was and was not tested\n\nThe historical snapshot is trustworthy for final-score models: 3,693 games, 30 teams, no duplicate game IDs, no missing scores, no ties, a matching SHA-256 receipt, and no feature-cutoff violations. It does **not** contain leakage-safe historical offensive rating, defensive rating, pace, effective field-goal percentage, turnover rate, rebound percentage, free-throw rate, lineups, injuries, or sportsbook prices. The evaluated gradient boost is therefore a score/form model—not a full box-score model—and no profitability claim is made." />
      <EvidenceTable id="forecasts-table" title="Every current baseline forecast" queryId="forecast_diagnostics"
        rows={forecasts} description="All 102 upcoming games with exact rating math, probability, selection, generation time, and feature cutoff."
        columns={[
          {field:"kickoff_time",label:"Tipoff (UTC)"},{field:"away_team",label:"Away"},{field:"home_team",label:"Home"},
          {field:"away_rating",label:"Away Elo"},{field:"home_rating",label:"Home Elo"},
          {field:"home_court_adjustment",label:"Home adj."},{field:"adjusted_home_rating_difference",label:"Adjusted diff"},
          {field:"home_win_probability",label:"Home probability",presentation:"percent"},{field:"predicted_winner",label:"Pick"},
          {field:"feature_data_cutoff",label:"Feature cutoff"},
        ]} />
    </section>

    <section className="report-section report-recommendations">
      <EvidenceNarrative id="recommendation" queryIds={["model_comparison", "current_summary"]}
        value="## Recommendation and controlled next steps\n\n**Keep `nba-game-elo-baseline-v1` unchanged.** It is a transparent research baseline and its historical identity should not be rewritten.\n\n**Continue `nba-game-logit-form-rest-research-v2` as the primary shadow candidate.** The staging repair now preserves one immutable official shadow prediction while appending a `LATEST_FORECAST` only when evidence changes; repeated runs remain idempotent, and the feature cutoff reflects observed evidence time.\n\nPriorities:\n\n1. Accumulate 300–500 prospectively frozen paired games before considering promotion.\n2. Monitor paired Brier, log loss, calibration, disagreement outcomes, and feature freshness.\n3. Design a versioned prior-season Elo state artifact before operationalizing the hybrid.\n4. Build a leakage-safe historical team box-score store before claiming a full statistics model.\n5. Require explicit founder approval, rollback rehearsal, and a reviewed registry action before any serving change." />
    </section>

    <RichNarrative id="report:disclosure" className="report-disclosure"
      value="**Evidence and governance boundary.** The current-season population was reconstructed from the repository's existing ESPN adapter for the exact 2026-10-01 through 2026-11-12 ingestion window; its primary monthly request returned HTTP 403 and the existing dated CDN fallback succeeded. Historical evaluation used the frozen 3,693-game artifact with a matching SHA-256 receipt. No production model, scheduler, prediction, grading record, NBA serving path, NFL V2 logic, parlay system, player-prop system, or wagering authorization was changed. The shadow lifecycle repair is staging code only until separately deployed and verified." />
  </article>;
}
