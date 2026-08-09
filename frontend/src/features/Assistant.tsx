/**
 * Asking the campaign questions in French.
 *
 * The familiar chat shape — a growing transcript, a text box, an attachment
 * button — because that is the one people already know and this screen has
 * nothing to teach them about its controls.
 *
 * Two things are deliberately *not* familiar. The scope is stated up front,
 * because an assistant that silently declines feels broken while one that says
 * what it covers feels bounded. And each answer carries what it was built from,
 * so a reply resting on figures the phase has not produced yet reads differently
 * from one resting on the whole dossier.
 *
 * The framing is a server-side setting. When more than one is configured the
 * picker appears and the answers carry which one produced them; with a single
 * profile — the case today — neither is shown, because neither would say
 * anything.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { assistantApi } from '../lib/api'
import type { AssistantProfile, AssistantTurn, Overview } from '../lib/types'
import { Alert, Badge, Button, Card, Icons, useErrorToast } from '../components/ui'

const MAX_FILES = 5

/** Openers that fit any phase, so an empty screen is never a blank prompt. */
const SUGGESTIONS = [
  'Où en est le comptage ?',
  'Quelles zones bloquent encore ?',
  'Quels sont les plus gros écarts, et pourquoi ?',
  'Qu’est-ce qui empêche de passer à la phase suivante ?',
]

type Message = AssistantTurn & {
  /** Only on assistant turns: the context blocks the answer was built from. */
  blocks?: string[]
  /** Which framing produced it — the picker can change between turns. */
  profile?: string
  files?: string[]
}

export function Assistant() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const showError = useErrorToast()

  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [profile, setProfile] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const bottom = useRef<HTMLDivElement>(null)

  const framings = useQuery({
    queryKey: ['assistant-profiles', campaignId],
    queryFn: () => assistantApi.profiles(campaignId),
    staleTime: Infinity,
  })
  // Until the server has said which framing is deployed, follow it rather than
  // guessing: a picker that starts on the wrong entry lies about the answers.
  const active = profile ?? framings.data?.active ?? null
  const current = framings.data?.profiles.find((p) => p.key === active) ?? null

  const ask = useMutation({
    mutationFn: async (question: string) => {
      // Only the prose travels as history: shipping the block names back would
      // spend context describing the context.
      const history: AssistantTurn[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }))
      return files.length
        ? assistantApi.askWithFiles(
            campaignId, question, history, files, active ?? undefined,
          )
        : assistantApi.ask(campaignId, question, history, active ?? undefined)
    },
    onSuccess: (result) => {
      setMessages((previous) => [
        ...previous,
        {
          role: 'assistant',
          content: result.answer,
          blocks: result.contextBlocks,
          profile: result.profile,
        },
      ])
    },
    onError: (error) => {
      showError(error, 'Question sans réponse')
      // The failed question goes back in the box rather than being lost.
      setMessages((previous) => {
        const last = previous[previous.length - 1]
        if (last?.role === 'user') setDraft(last.content)
        return previous.slice(0, -1)
      })
    },
  })

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, ask.isPending])

  const send = (question: string) => {
    const trimmed = question.trim()
    if (!trimmed || ask.isPending) return
    setMessages((previous) => [
      ...previous,
      { role: 'user', content: trimmed, files: files.map((f) => f.name) },
    ])
    setDraft('')
    ask.mutate(trimmed)
    setFiles([])
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      {/* A picker over a single entry is a control that does nothing. It comes
          back on its own the day a second framing is configured. */}
      {(framings.data?.profiles.length ?? 0) > 1 && (
        <ProfilePicker
          profiles={framings.data!.profiles}
          active={active}
          deployed={framings.data!.active}
          onChange={setProfile}
        />
      )}

      <div className="chat">
        {messages.length === 0 && !ask.isPending && (
          <EmptyConversation profile={current} onPick={send} />
        )}

        {messages.map((message, index) => (
          <Bubble key={index} message={message} />
        ))}

        {ask.isPending && (
          <div className="chat__row chat__row--assistant">
            <div className="chat__bubble chat__bubble--assistant subtle">
              {current?.context === 'none' ? 'Réflexion…' : 'Lecture de la campagne…'}
            </div>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <Card flush>
        <div className="composer">
          <textarea
            className="composer__input"
            rows={2}
            placeholder={
              current?.context === 'none'
                ? 'Posez n’importe quelle question…'
                : 'Posez une question sur cette campagne…'
            }
            value={draft}
            disabled={ask.isPending}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter breaks the line: the convention every
              // chat box already uses.
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                send(draft)
              }
            }}
          />

          {files.length > 0 && (
            <div className="chips" style={{ padding: '0 var(--space-3)' }}>
              {files.map((file) => (
                <span key={file.name} className="chip">
                  {file.name}
                  <button
                    className="chip__remove"
                    aria-label={`Retirer ${file.name}`}
                    onClick={() => setFiles(files.filter((f) => f !== file))}
                  >
                    <Icons.x size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="composer__actions">
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.csv,.md"
              onChange={(event) => {
                const picked = Array.from(event.target.files ?? [])
                setFiles((previous) => [...previous, ...picked].slice(0, MAX_FILES))
                event.target.value = ''
              }}
            />
            <Button
              variant="ghost"
              size="sm"
              icon={<Icons.upload size={14} />}
              disabled={ask.isPending || files.length >= MAX_FILES}
              onClick={() => fileInput.current?.click()}
              title="Joindre un PDF, une image ou un fichier texte"
            >
              Joindre
            </Button>
            <span className="spacer" />
            {messages.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setMessages([])}>
                Effacer
              </Button>
            )}
            <Button
              variant="primary"
              size="sm"
              icon={<Icons.sparkles size={14} />}
              disabled={!draft.trim() || ask.isPending}
              onClick={() => send(draft)}
            >
              Envoyer
            </Button>
          </div>
        </div>
      </Card>

      <p className="subtle" style={{ fontSize: 'var(--text-xs)' }}>
        {current?.context === 'none'
          ? 'Aucune donnée de la campagne n’est transmise dans ce mode.'
          : 'L’assistant lit le contexte de cette campagne.'}{' '}
        Il ne modifie rien. Vérifiez tout chiffre avant de l’utiliser dans une décision.
      </p>
    </div>
  )
}

/**
 * Choosing how the assistant is framed.
 *
 * Visible rather than buried in configuration, because the same question gets a
 * genuinely different answer in each mode — and somebody reading an answer needs
 * to know which one produced it. The deployed default is marked, so switching
 * away from it is a deliberate act.
 */
function ProfilePicker({
  profiles,
  active,
  deployed,
  onChange,
}: {
  profiles: AssistantProfile[]
  active: string | null
  deployed: string
  onChange: (key: string) => void
}) {
  const current = profiles.find((p) => p.key === active)
  return (
    <div className="row-wrap" style={{ gap: 'var(--space-3)', alignItems: 'center' }}>
      <div className="segmented">
        {profiles.map((profile) => (
          <button
            key={profile.key}
            className={`segmented__item${
              active === profile.key ? ' segmented__item--active' : ''
            }`}
            title={profile.description}
            onClick={() => onChange(profile.key)}
          >
            {profile.label}
            {profile.key === deployed && (
              <span className="segmented__count">défaut</span>
            )}
          </button>
        ))}
      </div>
      {current && <span className="subtle">{current.description}</span>}
    </div>
  )
}

function EmptyConversation({
  profile,
  onPick,
}: {
  profile: AssistantProfile | null
  onPick: (question: string) => void
}) {
  const open = profile?.context === 'none'
  return (
    <div className="stack" style={{ gap: 'var(--space-3)', padding: 'var(--space-4)' }}>
      <Alert
        tone={open ? 'warning' : 'info'}
        title={open ? 'Mode libre' : 'Ce que l’assistant couvre'}
      >
        {profile?.scopeNote ??
          'Les données, l’avancement, les écarts et les contrôles de cette campagne.'}
      </Alert>
      {!open && (
        <div className="chips">
          {SUGGESTIONS.map((question) => (
            <button key={question} className="chip" onClick={() => onPick(question)}>
              {question}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Bubble({ message }: { message: Message }) {
  const mine = message.role === 'user'
  return (
    <div className={`chat__row chat__row--${mine ? 'user' : 'assistant'}`}>
      <div className={`chat__bubble chat__bubble--${mine ? 'user' : 'assistant'}`}>
        {mine ? message.content : <Markdown text={message.content} />}
        {message.files?.length ? (
          <div className="chat__meta">{message.files.join(' · ')}</div>
        ) : null}
        {!mine && (message.blocks?.length || message.profile) ? (
          <div className="chat__meta row-wrap" style={{ gap: 'var(--space-1)' }}>
            {message.profile && <Badge tone="accent">{message.profile}</Badge>}
            {message.blocks?.length ? (
              <>
                <span>d’après :</span>
                {message.blocks.map((block) => (
                  <Badge key={block} tone="neutral">
                    {block}
                  </Badge>
                ))}
              </>
            ) : (
              <span>sans contexte de campagne</span>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/**
 * The small subset of markdown the assistant is asked to produce.
 *
 * A full renderer would be a dependency and an injection surface for the sake
 * of bullets and bold. Everything here writes text nodes, never HTML.
 */
function Markdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/)
  return (
    <>
      {blocks.map((block, index) => {
        const lines = block.split('\n')
        const bullets = lines.every((line) => /^\s*[-*•]\s+/.test(line))
        if (bullets) {
          return (
            <ul key={index} style={{ margin: 0, paddingLeft: '1.1rem' }}>
              {lines.map((line, i) => (
                <li key={i}>{inline(line.replace(/^\s*[-*•]\s+/, ''))}</li>
              ))}
            </ul>
          )
        }
        return <p key={index}>{lines.map((line, i) => [inline(line), <br key={i} />])}</p>
      })}
    </>
  )
}

function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      part
    ),
  )
}
