/* "Data may be out of date" note, shared by the landing page and both dashboards.

   Mirrors pipeline_common/freshness.py (the weekly GitHub watchdog), so the site and the
   email alert use one rule: NHS England publishes each month's figures roughly 6 weeks after
   the month ends, so data counts as STALE once more than GRACE_DAYS (12 weeks) have passed
   since the end of its latest month — i.e. a monthly release has very likely been missed.
   Keep GRACE_DAYS in step with the Python constant (a test checks this). */
(function(){
  const GRACE_DAYS = 84;
  const MONTHS = ["January","February","March","April","May","June","July","August",
                  "September","October","November","December"];

  // 'YYYY-MM' -> the first day (local midnight) on which that data counts as stale.
  function staleFrom(ym){
    const [y, m] = ym.split("-").map(Number);
    const monthEnd = new Date(y, m, 0);              // day 0 of next month = last day of this one
    return new Date(y, m - 1, monthEnd.getDate() + GRACE_DAYS + 1);
  }
  function isStale(ym, today){
    const t = today || new Date();
    const d = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    return d >= staleFrom(ym);
  }
  function label(ym){ const [y, m] = ym.split("-").map(Number); return MONTHS[m - 1] + " " + y; }

  // Fill `el` with the note and un-hide it when `latestMonth` is stale; otherwise keep it hidden.
  function show(el, latestMonth, what){
    if(!el || !latestMonth || !isStale(latestMonth)) return false;
    el.innerHTML = "&#9888; <strong>Data may be out of date.</strong> " +
      (what ? what + ": the" : "The") + " latest month shown is " + label(latestMonth) +
      ". NHS England normally publishes newer figures within about six weeks of a month " +
      "ending, so an update appears to have been missed.";
    el.hidden = false;
    return true;
  }

  window.Freshness = { GRACE_DAYS, staleFrom, isStale, show };
})();
