import { a as interpPolar, r as allocOutputs } from "./spring-B_P92nf6.js";
import { i as serialize } from "./normalize-CYnN3Npw.js";
import { a as buildPlan, t as resampleIcon } from "./resample-CXlaZ5ZQ.js";
import { a as paintOf } from "./region-BjECuWZR.js";
import { canonicalD, createMorph, restAttrs } from "./dom.js";
//#region src/dom/controller.ts
/** The endpoint a frozen controlled pair renders at rest at `t` (SSR
*  included), or null mid-way for a stroke pair, interpolated with the pure
*  core. A pair with a fill endpoint scrubs as a region morph, which only the
*  lazy chunk draws: mid-way it renders `from` until the client's first seek. */
function frozenEnd(from, to, t) {
	if (t >= 1) return to;
	if (t <= 0 || paintOf(from) === "fill" || paintOf(to) === "fill") return from;
	return null;
}
/** Frozen shape of the from→to pair at t, using the pure core (SSR-safe).
*  At exact endpoints returns the canonical `d` (real curves, not polyline). */
function frozenD(from, to, t) {
	const end = frozenEnd(from, to, t);
	if (end !== null) return canonicalD(end);
	const plan = buildPlan(resampleIcon(from), resampleIcon(to));
	const out = allocOutputs(plan);
	interpPolar(plan, t, out);
	return serialize(out, plan.items.map((it) => it.closed));
}
/** The initial d is a constant for every binding: computed once from the
*  mount-time props (server and client produce the same string → hydration
*  without mismatch) and from then on only the driver mutates it outside the
*  template. */
function computeInitialD({ icon, from, to, progress }) {
	if (from !== void 0 && to !== void 0) return frozenD(from, to, progress ?? 0);
	const first = icon ?? from ?? to;
	return first !== void 0 ? canonicalD(first) : "";
}
/** Paint attributes of the initial `<path>` (SSR included), a constant like
*  the initial d: a fill icon is filled before any script runs. A frozen
*  stroke pair paints as the endpoint it is nearer to; one with a fill
*  endpoint rests on `from` mid-way, like its d (frozenEnd). */
function computeInitialPaint({ icon, from, to, progress }) {
	if (from !== void 0 && to !== void 0) {
		const t = progress ?? 0;
		return restAttrs(frozenEnd(from, to, t) ?? (t < .5 ? from : to));
	}
	return restAttrs(icon ?? from ?? to);
}
/** Per-instance driver state — the exact logic of the React/Vue bindings
*  (mount, mode watch, controlled seek with re-basing, imperative). Takes the
*  init-time props: they seed the watch baselines so a watcher that fires on
*  mount (Svelte's $effect, unlike Vue's watch) is a no-op. All change
*  detection lives HERE, under tsc — the shells only wire their reactive
*  surface to these methods.
*
*  Lifecycle contract (shared verbatim by the bindings):
*  - Lazy driver: an iconless mount keeps the element and births the driver
*    on the FIRST icon that shows up (prop or imperative). `morphTo` with no
*    driver behaves as `set` — there is nothing to fly from.
*  - Controlled wins: while `from` and `to` are both present the pair owns
*    the path and `icon` changes are ignored; dropping the pair hands the
*    path back to `icon`.
*  - Every exit from controlled mode (imperative call or icon takeover)
*    invalidates the frozen pair, so returning to it re-bases on `from`. */
function createController({ icon, from, to, progress, reducedMotion, strokeWidth }) {
	let el = null;
	let dead = false;
	let morph = null;
	let rm = reducedMotion ?? "never";
	let sw = Number(strokeWidth ?? 2);
	let based = false;
	let pair = null;
	let prevIcon = icon;
	let prevControlled = from !== void 0 && to !== void 0;
	let prevFrom = from;
	let prevTo = to;
	let prevProgress = progress;
	/** Driver birth, lazy included: the first icon to show up creates it. */
	const ensure = (birth) => {
		if (morph) return morph;
		if (dead || !el) return null;
		morph = createMorph(el, birth, {
			reducedMotion: rm,
			strokeWidth: sw
		});
		return morph;
	};
	/** Controlled mode: freeze the pair at `progress` via seek (no spring). */
	const applyPair = (from, to, progress) => {
		const m = morph;
		if (!m) return;
		const t = progress ?? 0;
		if (!pair || pair[0] !== from || pair[1] !== to) {
			pair = [from, to];
			based = false;
		}
		if (t <= 0) {
			m.set(from);
			based = false;
		} else if (t >= 1) {
			m.set(to);
			based = false;
		} else {
			if (!based) {
				m.set(from);
				based = true;
			}
			m.seek(to, t);
		}
	};
	return {
		mount(mountEl, { icon, from, to, progress, reducedMotion, strokeWidth }) {
			el = mountEl;
			rm = reducedMotion ?? rm;
			sw = Number(strokeWidth ?? sw);
			const controlled = from !== void 0 && to !== void 0;
			const initialIcon = icon ?? from ?? to;
			if (initialIcon === void 0) return;
			const m = createMorph(el, controlled ? from : initialIcon, {
				reducedMotion: rm,
				strokeWidth: sw
			});
			morph = m;
			if (controlled) {
				pair = [from, to];
				const t = progress ?? 0;
				if (t <= 0) m.set(from);
				else if (t >= 1) m.set(to);
				else {
					m.seek(to, t);
					based = true;
				}
			}
		},
		destroy() {
			dead = true;
			el = null;
			morph?.destroy();
			morph = null;
			based = false;
			pair = null;
		},
		/** Prop watcher: ONE owner decides per run — the controlled pair while it
		*  is fully present, `icon` otherwise (mount doesn't fire thanks to the
		*  init-time baselines). */
		watch({ icon, from, to, progress, spring, reducedMotion, strokeWidth }) {
			rm = reducedMotion ?? "never";
			sw = Number(strokeWidth ?? 2);
			if (morph) {
				morph.reducedMotion = rm;
				morph.strokeWidth = sw;
			}
			const controlled = from !== void 0 && to !== void 0;
			const left = prevControlled && !controlled;
			const iconChanged = icon !== prevIcon;
			const pairChanged = from !== prevFrom || to !== prevTo || progress !== prevProgress;
			prevControlled = controlled;
			prevIcon = icon;
			prevFrom = from;
			prevTo = to;
			prevProgress = progress;
			if (controlled) {
				if (!pairChanged) return;
				if (!(morph ?? ensure(from))) return;
				applyPair(from, to, progress);
				return;
			}
			if (icon === void 0 || !iconChanged && !left) return;
			pair = null;
			based = false;
			if (morph) morph.morphTo(icon, spring);
			else ensure(icon);
		},
		morphTo(icon, spring) {
			pair = null;
			based = false;
			if (morph) morph.morphTo(icon, spring);
			else ensure(icon);
		},
		set(icon) {
			pair = null;
			based = false;
			if (morph) morph.set(icon);
			else ensure(icon);
		}
	};
}
//#endregion
export { frozenEnd as i, computeInitialPaint as n, createController as r, computeInitialD as t };
