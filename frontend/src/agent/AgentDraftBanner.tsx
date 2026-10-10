/**
 * Плашка черновика агента в переписке — над полем ответа того письма, на
 * которое черновик написан (`threads/ThreadPage`, одна строка).
 *
 * Показывает последний черновик переписки, пока по нему не решили:
 * - **готов** (`drafted`) — янтарём: «Подходит · отправить», «Править» →
 *   «Отправить правку», «Отклонить» с причиной;
 * - **отдан человеку** (`escalated`) — розой и без «как есть»: только правка
 *   или отклонение. Сервер держит то же: 409 и на «как есть», и на правку
 *   прежним текстом;
 * - **ответ не нужен** (`skipped`) — строкой: молчание агента видно, а оспаривают
 *   его ответом в поле внизу ленты (`threads/Composer`).
 *
 * Причина отклонения — выбор из списка этапа (`agent_reasons` переписки) или
 * «другое» словами: без неё кнопка неактивна, а сервер отвечает 422.
 * «Черновик устарел» — переписка ушла дальше письма черновика — решает
 * сервер: 409 его словами. Ответить своими словами черновик не мешает —
 * подсказка у поля ответа только напоминает о нём.
 *
 * Переписку плашка берёт из запроса экрана переписки по номеру из адреса
 * и своего запроса не делает.
 */

import { Badge, Button, Card, Group, Radio, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router-dom';

import { rejectDraft, sendDraft } from '../api/agent';
import type { DraftCard, ThreadAgent } from '../api/agent';
import { refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { fetchThread } from '../api/outreach';
import type { ThreadView } from '../api/thread';
import type { SendResult } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { notify } from '../notices';

/** По этим черновикам ещё есть что решать. */
const LIVE = new Set(['drafted', 'escalated', 'skipped']);

const OTHER = 'другое';
/** «Другое» словами — коротко: это причина, а не письмо. */
const OTHER_MAX = 480;

const VERDICTS = {
  allow: 'судья: пропустил',
  block: 'судья: вернул на правку',
  escalate: 'судья: человеку',
} as const;

type Mode = 'idle' | 'edit' | 'reject';

/** Последний черновик переписки — к самому позднему ответу собеседника. */
function latestOf(drafts: DraftCard[]): DraftCard | undefined {
  return drafts.reduce<DraftCard | undefined>(
    (last, draft) => (last === undefined || draft.reply_id > last.reply_id ? draft : last),
    undefined,
  );
}

export function AgentDraftBanner({ replyId }: { replyId: number }) {
  const threadId = rowIdOf(useParams<{ id: string }>().id);
  // Тот же запрос, что у экрана переписки, — только наблюдатель: грузит экран.
  const { data } = useQuery({
    queryKey: ['thread', String(threadId)],
    queryFn: async () => (await fetchThread(threadId ?? 0)) as ThreadView & ThreadAgent,
    enabled: false,
  });
  const draft = latestOf(data?.drafts ?? []);
  if (threadId === null || data === undefined || draft === undefined) return null;
  if (draft.reply_id !== replyId || !LIVE.has(draft.status)) return null;
  // Ход — какой по счёту ответ собеседника этот черновик отвечает.
  const turn = data.incoming.filter((one) => one.kind === 'human' && one.id <= replyId).length;
  const props = { threadId, draft, turn, reasons: data.agent_reasons ?? [] };
  // Новый текст черновика («написать заново») — новая плашка, без старой правки.
  const key = `${draft.id}-${draft.written_at}`;
  return draft.status === 'skipped' ? (
    <SkippedLine key={key} draft={props.draft} />
  ) : (
    <Banner key={key} {...props} />
  );
}

interface DraftProps {
  threadId: number;
  draft: DraftCard;
  turn: number;
  reasons: string[];
}

function sentWords(sent: SendResult): string {
  return sent.real
    ? `Ответ отправлен с ${sent.sender_email}`
    : 'Ответ записан, но не ушёл: почта выключена (транспорт null)';
}

/** Отправить и отклонить — с обновлением переписки и отказом сервера словами. */
function useDecision(threadId: number, draft: DraftCard) {
  const queryClient = useQueryClient();
  async function settled(message: string, color: string) {
    // Список диалогов тоже меняется: переписка перестаёт ждать человека.
    await queryClient.invalidateQueries({ queryKey: ['thread', String(threadId)] });
    await queryClient.invalidateQueries({ queryKey: ['threads'] });
    notify({ message, color });
  }
  const send = useMutation({
    mutationFn: (body: string | null) => sendDraft(draft.id, body),
    onSuccess: (sent) => settled(sentWords(sent), sent.real ? 'green' : 'yellow'),
    onError: (failure) =>
      notify({ title: 'Не отправили', message: refusalOf(failure), color: 'red' }),
  });
  const reject = useMutation({
    mutationFn: (reason: string) => rejectDraft(draft.id, reason),
    onSuccess: () => settled('Черновик отклонён, причина записана', 'green'),
    onError: (failure) =>
      notify({ title: 'Не отклонили', message: refusalOf(failure), color: 'red' }),
  });
  return { send, reject, busy: send.isPending || reject.isPending };
}

/**
 * «Ответ не нужен» — строкой: молчание агента видно, а оспаривается оно ответом
 * в поле внизу ленты (`threads/Composer`) — этот ответ и закрывает черновик на
 * сервере. До 10.10.2026 здесь было своё поле «Ответить всё же»: с полем ответа,
 * которое теперь видно сразу, на экране вставали два поля и две «Отправить».
 */
function SkippedLine({ draft }: { draft: DraftCard }) {
  const { can } = useSession();
  return (
    <Group gap="xs" mt="sm">
      <Badge variant="light" color="gray">
        агент: ответ не нужен
      </Badge>
      <Text size="sm">{draft.reason ?? 'причина не названа'}</Text>
      {can('send') ? (
        <Text size="sm" c="dimmed">
          Не согласны — ответьте в поле внизу.
        </Text>
      ) : null}
    </Group>
  );
}

/** Причина словами одной фразой: своя точка в конце не удваивается, нет причины — нет и фразы. */
function whyOf(reason: string | null): string {
  const said = (reason ?? '').trim().replace(/[.\s]+$/, '');
  return said === '' ? '' : `Почему: ${said}. `;
}

function Badges({ draft, turn }: { draft: DraftCard; turn: number }) {
  const escalated = draft.status === 'escalated';
  return (
    <Group gap="xs">
      <Badge variant="light" color={escalated ? 'red' : 'yellow'}>
        {escalated ? 'агент отдал ответ человеку' : 'черновик агента'}
      </Badge>
      <Badge variant="light" color="gray">
        ход {turn}
      </Badge>
      {draft.verdict !== null ? (
        <Badge variant="light" color="gray">
          {VERDICTS[draft.verdict]}
        </Badge>
      ) : null}
      {draft.attempts > 0 ? (
        <Badge variant="light" color="gray">
          попыток: {draft.attempts}
        </Badge>
      ) : null}
    </Group>
  );
}

/** Готовый или отданный человеку черновик — плашкой с решениями. */
function Banner({ threadId, draft, turn, reasons }: DraftProps) {
  const { can } = useSession();
  const [mode, setMode] = useState<Mode>('idle');
  const escalated = draft.status === 'escalated';
  return (
    <>
      <Card
        component="section"
        aria-label="Черновик агента"
        className="glassQuiet"
        p="md"
        mt="sm"
        // Кромка — смысл цветом: янтарь — нужно внимание, роза — отказ.
        style={{
          borderLeft: `4px solid var(--mantine-color-${escalated ? 'red' : 'yellow'}-6)`,
        }}
      >
        <Stack gap="xs">
          <Badges draft={draft} turn={turn} />
          {escalated ? (
            <Text size="sm">
              {whyOf(draft.reason)}Как есть такой ответ не уходит — поправьте его или ответьте сами.
            </Text>
          ) : null}
          {draft.body.trim() !== '' && mode !== 'edit' ? (
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {draft.body}
            </Text>
          ) : null}
          {can('send') ? (
            <Actions
              threadId={threadId}
              draft={draft}
              reasons={reasons}
              mode={mode}
              onMode={setMode}
            />
          ) : (
            <Text size="sm" c="dimmed">
              Отправить или отклонить черновик может тот, у кого есть право отправки писем.
            </Text>
          )}
        </Stack>
      </Card>
      {!escalated && can('send') ? (
        <Text size="sm" c="dimmed" mt="xs">
          Есть черновик агента выше. Ответить можно и своими словами — черновик тогда закроется
          вашим ответом.
        </Text>
      ) : null}
    </>
  );
}

interface ActionsProps {
  threadId: number;
  draft: DraftCard;
  reasons: string[];
  mode: Mode;
  onMode: (mode: Mode) => void;
}

/** Решения по черновику: кнопки, поле правки или выбор причины. */
function Actions({ threadId, draft, reasons, mode, onMode }: ActionsProps) {
  const decision = useDecision(threadId, draft);
  const [text, setText] = useState(draft.body);
  const escalated = draft.status === 'escalated';
  const typed = text.trim();
  // У отданного человеку прежний текст — тот же «как есть»: сервер его не пустит.
  const edited = typed !== '' && (!escalated || typed !== draft.body.trim());
  if (mode === 'edit') {
    return (
      <Editor
        text={text}
        onText={setText}
        action="Отправить правку"
        ready={edited}
        busy={decision.busy}
        onSend={() => decision.send.mutate(typed)}
        onCancel={() => onMode('idle')}
      />
    );
  }
  if (mode === 'reject') {
    return (
      <Reasons
        reasons={reasons}
        busy={decision.busy}
        onReject={(reason) => decision.reject.mutate(reason)}
        onCancel={() => onMode('idle')}
      />
    );
  }
  return (
    <Group gap="xs">
      {escalated ? null : (
        <Button
          color="lagoon"
          className="press"
          loading={decision.send.isPending}
          disabled={decision.busy}
          onClick={() => decision.send.mutate(null)}
        >
          Подходит · отправить
        </Button>
      )}
      <Button variant="light" disabled={decision.busy} onClick={() => onMode('edit')}>
        Править
      </Button>
      <Button variant="light" disabled={decision.busy} onClick={() => onMode('reject')}>
        Отклонить
      </Button>
    </Group>
  );
}

interface ReasonsProps {
  reasons: string[];
  busy: boolean;
  onReject: (reason: string) => void;
  onCancel: () => void;
}

/** Причина отклонения — выбором из списка этапа или словами; без неё — нельзя. */
function Reasons({ reasons, busy, onReject, onCancel }: ReasonsProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [other, setOther] = useState('');
  const said = other.trim();
  const reason = picked === OTHER ? (said === '' ? null : `${OTHER}: ${said}`) : picked;
  return (
    <Stack gap="xs">
      <Radio.Group label="Почему отклоняете" value={picked} onChange={setPicked}>
        <Stack gap={6} mt={6}>
          {[...reasons, OTHER].map((one) => (
            <Radio key={one} value={one} label={one === OTHER ? 'другое — словами' : one} />
          ))}
        </Stack>
      </Radio.Group>
      {picked === OTHER ? (
        <TextInput
          aria-label="Причина словами"
          maxLength={OTHER_MAX}
          value={other}
          onChange={(event) => setOther(event.currentTarget.value)}
        />
      ) : null}
      <Group>
        <Button
          color="lagoon"
          className="press"
          loading={busy}
          disabled={reason === null}
          onClick={() => reason !== null && onReject(reason)}
        >
          Отклонить
        </Button>
        <Button variant="subtle" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
      </Group>
    </Stack>
  );
}

interface EditorProps {
  text: string;
  onText: (text: string) => void;
  action: string;
  ready: boolean;
  busy: boolean;
  onSend: () => void;
  onCancel: () => void;
}

/** Поле правки: текст уходит только по кнопке, как у ответа из переписки. */
function Editor({ text, onText, action, ready, busy, onSend, onCancel }: EditorProps) {
  return (
    <Stack gap="xs">
      <Textarea
        aria-label="Текст ответа по черновику"
        autosize
        minRows={4}
        value={text}
        onChange={(event) => onText(event.currentTarget.value)}
      />
      <Group>
        <Button color="lagoon" className="press" loading={busy} disabled={!ready} onClick={onSend}>
          {action}
        </Button>
        <Button variant="subtle" disabled={busy} onClick={onCancel}>
          Отмена
        </Button>
      </Group>
    </Stack>
  );
}
