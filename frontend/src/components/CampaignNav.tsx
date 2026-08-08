/**
 * The campaign's navigation, in the sidebar.
 *
 * One vertical surface carries all three levels — phase, section, sub-section —
 * so nothing is left for a horizontal bar. Two stacked bars of identical shape
 * were the problem: nothing told the eye which one answered "where am I" and
 * which answered "what am I looking at". Here the levels are distinguished by
 * position and weight, which the eye reads without being taught.
 *
 * The phase headings do double duty: they group the sections *and* say where
 * the campaign is in its life, which is what the separate stepper used to do
 * with a whole band of its own.
 */

import { NavLink, useLocation } from 'react-router-dom'
import type { Overview } from '../lib/types'
import { PHASE_GROUPS, SECTIONS, type Section } from '../lib/navigation'
import { subSectionPath, VIEW_PARAM } from '../lib/subsection'
import { useFocusMode } from '../lib/focus'
import { Icons } from './ui'

const STATUS_ORDER = ['PREPARATION', 'COUNTING', 'ANALYSIS', 'CLOSED']

export function CampaignNav({ overview }: { overview: Overview }) {
  const location = useLocation()
  const [focus] = useFocusMode()
  const base = `/campagnes/${overview.campaign.id}`
  const current = STATUS_ORDER.indexOf(overview.campaign.status)

  return (
    <>
      {PHASE_GROUPS.map((group) => {
        const sections = SECTIONS.filter((s) => s.phase === group.id)
        if (sections.length === 0) return null
        const rank = group.status ? STATUS_ORDER.indexOf(group.status) : -1
        const state =
          rank < 0 ? '' : rank < current ? 'done' : rank === current ? 'current' : 'ahead'

        return (
          <div key={group.id} className="navgroup">
            <div className={`navgroup__label navgroup__label--${state || 'plain'}`}>
              {group.label}
              {state === 'current' && <span className="navgroup__now">en cours</span>}
              {state === 'done' && <Icons.check size={12} />}
            </div>
            {sections.map((section) => (
              <SectionLink
                key={section.to}
                section={section}
                base={base}
                overview={overview}
                focus={focus}
                open={isOpen(section, location.pathname, base)}
                view={new URLSearchParams(location.search).get(VIEW_PARAM)}
              />
            ))}
          </div>
        )
      })}
    </>
  )
}

function isOpen(section: Section, pathname: string, base: string): boolean {
  const target = section.to ? `${base}/${section.to}` : base
  return section.to ? pathname.startsWith(target) : pathname === base
}

function SectionLink({
  section,
  base,
  overview,
  focus,
  open,
  view,
}: {
  section: Section
  base: string
  overview: Overview
  focus: boolean
  open: boolean
  view: string | null
}) {
  const Icon = Icons[section.icon]
  const enabled = section.enabled(overview)
  const badge = section.badge?.(overview, focus)
  const target = section.to ? `${base}/${section.to}` : base
  const fallback = section.subs?.[0]?.id ?? ''
  const active = view && section.subs?.some((s) => s.id === view) ? view : fallback

  return (
    <>
      <NavLink
        to={target}
        end={section.to === ''}
        className={({ isActive }) =>
          `navlink${isActive ? ' navlink--active' : ''}${enabled ? '' : ' navlink--disabled'}`
        }
        aria-disabled={!enabled}
        title={enabled ? undefined : section.locked?.(overview)}
      >
        <span className="navlink__icon">
          <Icon size={17} />
        </span>
        <span>{section.label}</span>
        {badge ? <span className="navlink__count num">{badge}</span> : null}
      </NavLink>

      {open && enabled && section.subs && (
        <div className="subnav">
          {section.subs.map((sub, index) => {
            const heading =
              sub.group && sub.group !== section.subs?.[index - 1]?.group ? sub.group : null
            const count = sub.count?.(overview)
            return (
              <div key={sub.id}>
                {heading && <div className="subnav__heading">{heading}</div>}
                <NavLink
                  to={subSectionPath(target, sub.id, fallback)}
                  className={`subnav__link${active === sub.id ? ' subnav__link--active' : ''}`}
                >
                  <span className="truncate">{sub.label}</span>
                  {count ? <span className="subnav__count num">{count}</span> : null}
                </NavLink>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}
