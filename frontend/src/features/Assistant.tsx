/**
 * Asking the campaign questions in French.
 *
 * The familiar chat shape — a growing transcript, a text box, an attachment
 * button — because that is the one people already know and this screen has
 * nothing to teach them about its controls.
 *
 * Two things are deliberately *not* familiar. The scope is stated up front and
 * repeated in the empty state, because an assistant that silently declines
 * feels broken while one that says what it covers feels bounded. And each
 * answer carries what it was built from: a reply resting on figures the phase
 * has not produced yet is worth reading differently from one resting on the
 * whole dossier.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { assistantApi } from '../lib/api'
import type { AssistantTurn, Overview } from '../lib/types'
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
  files?: string[]
}

export function Assistant() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const showError = useErrorToast()

  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const fileInput = useRef<HTMLInputElement>(null)
  const bottom = useRef<HTMLDivElement>(null)

  const ask = useMutation({
    mutationFn: async (question: string) => {
      // Only the prose travels as history: shipping the block names back would
      // spend context describing the context.
      const history: AssistantTurn[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }))
      return files.length
        ? assistantApi.askWithFiles(campaignId, question, history, files)
        : assistantApi.ask(campaignId, question, history)
    },
    onSuccess: (result) => {
      setMessages((previous) => [
        ...previous,
        { role: 'assistant', content: result.answer, blocks: result.contextBlocks },
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
      <div className="chat">
        {messages.length === 0 && !ask.isPending && (
          <EmptyConversation onPick={send} />
        )}

        {messages.map((message, index) => (
          <Bubble key={index} message={message} />
        ))}

        {ask.isPending && (
          <div className="chat__row chat__row--assistant">
            <div className="chat__bubble chat__bubble--assistant subtle">
              Lecture de la campagne…
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
            placeholder="Posez une question sur cette campagne…"
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
        L’assistant lit un condensé de cette campagne et ne modifie rien. Vérifiez tout
        chiffre avant de l’utiliser dans une décision.
      </p>
    </div>
  )
}

function EmptyConversation({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="stack" style={{ gap: 'var(--space-3)', padding: 'var(--space-4)' }}>
      <Alert tone="info" title="Ce que l’assistant couvre">
        Les données, l’avancement, les écarts, les contrôles et le fonctionnement de
        cette campagne. Rien d’autre.
      </Alert>
      <div className="chips">
        {SUGGESTIONS.map((question) => (
          <button key={question} className="chip" onClick={() => onPick(question)}>
            {question}
          </button>
        ))}
      </div>
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
        {message.blocks?.length ? (
          <div className="chat__meta row-wrap" style={{ gap: 'var(--space-1)' }}>
            <span>D’après :</span>
            {message.blocks.map((block) => (
              <Badge key={block} tone="neutral">
                {block}
              </Badge>
            ))}
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
