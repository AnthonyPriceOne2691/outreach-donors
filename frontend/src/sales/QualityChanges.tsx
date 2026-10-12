/**
 * Последние правки и отклонения черновиков агента — «черновик → ушло» с причиной.
 *
 * Карта правок оператора — это и есть то, в чём агент ошибается: короче, точнее, без
 * отсрочки. Разницу по словам считает сервер (`calibration_board.word_diff`): слева черновик
 * агента, убранное зачёркнуто; справа ушедшее, дописанное подчёркнуто. Цвет — второй признак,
 * а не единственный: зачёркнутое и подчёркнутое читаются и без него. У отклонения справа —
 * причина оператора; у черновика, который агент отдал человеку, — почему отдал.
 *
 * Ссылка «Открыть диалог» ведёт в переписку: разбирать ошибку — по всему разговору. Видны
 * первые пять, остальные — по кнопке, раскрытием в два такта (`runs/Unfold`).
 */

import { Badge, Button, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { ChangeRow, DiffKind, DiffPart } from '../api/salesQuality';
import { formatDateTime } from '../format';
import { Unfold } from '../runs/Unfold';

/** Текст стороны — общее и своё: у черновика убранное, у ушедшего дописанное. */
function Side({ parts, own }: { parts: DiffPart[]; own: Exclude<DiffKind, 'same'> }) {
  return (
    <Text size="sm" className="qualityText" style={{ whiteSpace: 'pre-wrap' }}>
      {parts
        .filter((part) => part.kind === 'same' || part.kind === own)
        .map((part, index) => {
          if (part.kind === 'same') return <span key={index}>{part.text}</span>;
          const removed = part.kind === 'removed';
          return (
            // `td` — явно: `Text` снимает с `del` и `ins` их черту, и правка читалась бы
            // одним цветом (замер на стенде 12.10.2026).
            <Text
              key={index}
              component={removed ? 'del' : 'ins'}
              inherit
              td={removed ? 'line-through' : 'underline'}
              c={removed ? 'var(--status-rose-ink)' : 'var(--status-mint-ink)'}
              className={removed ? 'qualityRemoved' : 'qualityAdded'}
            >
              {part.text}
            </Text>
          );
        })}
    </Text>
  );
}

function Plain({ text }: { text: string }) {
  return (
    <Text size="sm" className="qualityText" style={{ whiteSpace: 'pre-wrap' }}>
      {text}
    </Text>
  );
}

function Head({ change }: { change: ChangeRow }) {
  const edited = change.decision === 'edited';
  return (
    <Group justify="space-between" gap="xs" wrap="wrap">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" fw={600}>
          {change.title}
        </Text>
        {change.turn !== null ? (
          <Text size="xs" c="dimmed">
            ход {change.turn}
          </Text>
        ) : null}
        <Badge variant="light" color={edited ? 'yellow' : 'red'}>
          {edited ? 'с правкой' : 'отклонено'}
        </Badge>
        {change.held ? (
          <Badge variant="light" color="gray">
            отдан человеку
          </Badge>
        ) : null}
        <Text size="xs" c="dimmed">
          {formatDateTime(change.decided_at)}
        </Text>
      </Group>
      {change.thread_id !== null ? (
        <Button
          component={Link}
          to={`/threads/${change.thread_id}`}
          variant="light"
          size="compact-sm"
          className="press"
          rightSection={<IconArrowRight size={16} />}
        >
          Открыть диалог
        </Button>
      ) : null}
    </Group>
  );
}

/** Правая сторона: у правки — что ушло (с разметкой, если она есть), у отклонения — причина. */
function After({ change, marked }: { change: ChangeRow; marked: boolean }) {
  if (change.decision === 'rejected') return <Plain text={change.reason ?? 'без причины'} />;
  return marked ? <Side parts={change.parts} own="added" /> : <Plain text={change.sent ?? ''} />;
}

function Change({ change }: { change: ChangeRow }) {
  const marked = change.parts.length > 0;
  return (
    <Card component="article" className="glassQuiet qualityChange" p="md">
      <Stack gap="sm">
        <Head change={change} />
        {change.held && change.held_reason !== null ? (
          <Text size="xs" c="dimmed" className="qualityHeld">
            Агент отдал человеку: {change.held_reason}
          </Text>
        ) : null}
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Черновик агента
            </Text>
            {marked ? <Side parts={change.parts} own="removed" /> : <Plain text={change.draft} />}
          </Stack>
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              {change.decision === 'edited' ? 'Ушло' : 'Причина отклонения'}
            </Text>
            <After change={change} marked={marked} />
          </Stack>
        </SimpleGrid>
      </Stack>
    </Card>
  );
}

/** Сколько правок видно сразу; остальные — по кнопке: двадцать карточек — экран в шесть
 *  тысяч пикселей (снимок стенда 12.10.2026), и таблицы над ними уезжали из виду. */
export const CHANGES_SHOWN = 5;
const REST_ID = 'quality-changes-rest';

export function QualityChanges({ changes }: { changes: ChangeRow[] }) {
  const [open, setOpen] = useState(false);
  const first = changes.slice(0, CHANGES_SHOWN);
  const rest = changes.slice(CHANGES_SHOWN);
  return (
    <Stack gap="sm" px="md" component="section" aria-label="Последние правки и отклонения">
      <Title order={5}>Последние правки и отклонения</Title>
      {changes.length === 0 ? (
        <Text size="sm" c="dimmed" className="qualityNoChanges">
          Правок и отклонений по этой версии агента ещё нет.
        </Text>
      ) : (
        first.map((change) => <Change key={change.draft_id} change={change} />)
      )}
      {rest.length > 0 ? (
        <>
          <Unfold open={open}>
            <Stack gap="sm" id={REST_ID}>
              {rest.map((change) => (
                <Change key={change.draft_id} change={change} />
              ))}
            </Stack>
          </Unfold>
          {/* `aria-expanded` и `aria-controls` — состояние кнопки для программы чтения. */}
          <Group>
            <Button
              variant="subtle"
              size="compact-sm"
              className="press qualityMore"
              aria-expanded={open}
              aria-controls={REST_ID}
              onClick={() => setOpen((was) => !was)}
            >
              {open ? 'Свернуть' : `Показать ещё ${rest.length}`}
            </Button>
          </Group>
        </>
      ) : null}
    </Stack>
  );
}
