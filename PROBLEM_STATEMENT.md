# Problem Statement for "Ninja Times" project

## The Situation

The World Ninja League (WNL) Championships are, at the time I'm starting this (2026-06-20), underway. Over six days, thousands of athletes compete in multiple events divided into two tiers ("Tier 1" and "Tier 2"), and many divisions by age and gender.

Every athlete in every division gets to compete in a "Stage 1" event. If they place within the top 60% of competitors in that event, they progress to a "Stage 2" event. If they place within the top 70%W of competitors in that, they progress to a "Stage 3" event.

For each of those stages, the competitors are separated into waves based on seeding: "wave 1", "wave 2", etc. unless there are few enough that one wave is sufficient (often true for Stage 3). Each wave has a "run order" of competitors. For Stage 1, wave and run order assignments are based on regular-season performance. For successive stages, it's based on standings from the previous stage.

Most athletes — perhaps all, but I'm not certain — also compete in an event called the "Discipline Circuit". That has three sub-events: "Endurance", "Speed", and "Technical" ("Tech"). Athletes have different wave and run order assignments for this then they do for Stage 1. The run order is less directly interpretable, however, because the competitors in a wave are often split into sub-groups that rotate carousel-fashion between the three sub-events.

## The Problem

The WNL has a web site that has information about the locations and times of the events overall, as well as start times and run orders for each wave:

* `https://worldninjaleague.org/run-orders/`

However, it is quite inconvenient to navigate that web site to find relevant information about a specific competitor's wave and run order assignments and wave start time, especially for Stage 2 and Stage 3 which aren't determined or published until the prior stage's results are complete. There is no way to search by athlete, so one must continually click through to find and check the waves and run order listings. This is especially irritating when one is trying to keep track of multiple events for several athletes across divisions.

Complicating matters, the wave assignments and run orders for all divisions of a tier that use one particular rig (location) are combined on one long web page, with a list of the divisions at the top but no quick-jump-to links. This leads to lots and lots of scrolling. Searching by athlete name within a web page using browser search functionality helps, but is awkward on mobile.

Also both helpful and annoying is the fact that as athletes run in an event, they disappear from the run order list on that rig/division-wave page, so it shows upcoming athletes only… Except sometimes I find completed events are restored. I haven't persevered enough to tell whether there's a systematic rule in effect for this.

## The Goal

What I want is a tool that scrapes the WNL site to build a data table of the relevant location/wave/wave start time/run order position data for each athlete in a designated (short) list of athletes, or for only one athlete, and then presents that data in a useful, mobile-friendly table that can be filtered or sorted in various ways that I will specify. The tool should be able to re-check the WNL site when prompted, so that when information about Stage 2 or Stage 3 is added, it gets incorporated into the data table and display.

When information for a particular stage is not yet available for a particular athlete, that stage should still appear in the display, but indicating that wave assignment and run order are TBA, and listing the overall event (all-waves) start time instead, which is available on a different interactive page of the WNL site:

* `https://worldninjaleague.org/tier-1-championships/#sxi-schedule`

If a stage is posted and the athlete in question did not qualify for it, that should also be indicated. I'm imagining blank fields for wave and start time, and strike-through for all fields in that row. The UI details can be fine-tuned once the basic tool is up and running.

An important consideration is that I want this tool to be web-accessible to anyone with the link, which means hosting it as a web page somewhere convenient. I don't want to invest a lot in hosting infrastructure here, though I'm not adverse to doing a little work to set up something like a free-tier Cloudflare Pages account for that. However, if this tool can be built as a GitHub repo with GitHub Pages, even better.

For the athlete list: I think a good approach would be…

1. Have a text field where a viewer can enter an athlete's name (firstname lastname), or a comma-delimited list of names, and see all event info for that/those athlete(s).

2. Also, have a small multi-select box with a pre-populated list of names that I can hard-code into a configuration file (and easily modify), so that people can click/shift-click to select one or more of those, or click a button to select all of this "canonical set".

To give you a sense of what I'm looking for as output, I've hand-constructed a Google Sheets doc with info on a few athletes, with tabs for different sort orders (because anonymous viewers can't use sort-order tools). It won't auto-update or search info for a new name; it's just a totally static hand-built version. Take a look:

* https://docs.google.com/spreadsheets/d/1KdHzrxknrAw9lNBaO_nLBoFuu5iQAojVvbqVTxyTYfA/edit?gid=0#gid=0&fvid=1196546326

For development purposes, being able to run this locally for development and testing as well as hosting somewhere for public access would be really helpful.

## Your Task

1. Strategize, interactively with me, on a good tech stack and approach to accomplishing this. Keep in mind that this year's event is in progress and the info changes nearly-continually, and that I'd like a solution that is easy to adapt for next year's event when the 2027 site goes live. (We can assume for now that the WNL site's structure will be the same.)

2. Once we have a strategy mapped out, develop a detailed implementation plan. Make sure to include all the steps I have to take, such as creating accounts or changing repo settings, and where they fit in the overall development plan.

3. Once that's set, we can start the actual work.

This problem statement file is currently the only file in a local directory I've created to hold whatever we create. I can initialize it with git, set up a virtual environment with `uv`, create a repo on GitHub and clone it here, and/or whatever is necessary. This space is ours to use as we need.

___
