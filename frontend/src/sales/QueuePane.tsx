/**
 * Очередь писем продаж: подключены ли продажи, цепочка гипотезы, сборка и отправка пачкой.
 *
 * **Экран ничего не решает сам.** Подключены ли продажи и чего не хватает, полна ли
 * цепочка, сколько лидов ждёт письма — называет сервер (`GET /sales/queue`) тем же
 * правилом, каким откажут сборка и отправка. Не подключены — кнопки закрыты, и причины
 * названы словами отказа. Отказ сервера на нажатие (настройки сменились между чтением и
 * нажатием) — тоже словами: у кнопки сборки, а у пачки — словами общей кнопки.
 *
 * **Сборка — по гипотезе, пачка — по всем.** Сборка пишет первые письма лидам выбранной
 * гипотезы и ничего не отправляет: модель на каждое письмо — минуты, поэтому задачей.
 * Пачка — общая кнопка почты (`letters/SendQueue`) с этапом продаж, своей у продаж нет:
 * она берёт письма всех гипотез, кнопка называет их число, а вкладка говорит это словами
 * до нажатия. Кнопка пачки — только у кого есть право отправки.
 */

import {
  Alert,
  Badge,
  Button,
  Group,
  List,
  Loader,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { buildSalesQueue } from '../api/sales';
import { chainLanguageTitle } from '../api/salesLabels';
import type { ChainState, HypothesisCard, SalesQueueView } from '../api/salesTypes';
import { useSession } from '../auth/AuthProvider';
import { HintLabel } from '../components/HintLabel';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { JobLine } from '../jobs/JobLine';
import { SendQueue } from '../letters/SendQueue';
import { notify } from '../notices';
import { remember, remembered } from '../storage';
import { buildJobKey, QUEUE_QUERY_KEY, queueLine, useSalesQueue } from './queueData';

/** Писем за раз, пока человек не поправил: как у сборки писем доноров. */
const DEFAULT_LIMIT = 50;

function Connection({ view }: { view: SalesQueueView }) {
  if (view.connected) {
    return (
      <Group gap="xs" px="md">
        <Badge variant="light" color="green">
          продажи подключены
        </Badge>
        <Text size="sm" c="dimmed" className="queueConnected">
          Учётка почты, отписка и «Отправитель» на месте — письма соберутся и уйдут.
        </Text>
      </Group>
    );
  }
  return (
    <Alert
      color="yellow"
      title="Продажи к почте не подключены"
      mx="md"
      // Имя настройки сервера — одно слово длиннее телефона (`OUTREACH_SALES_SENDGRID_API_KEY`):
      // без переноса где угодно плашка растягивалась по нему и срезала строки справа на
      // 21 px (снимок 390 px, 09.10.2026).
      styles={{ message: { overflowWrap: 'anywhere' } }}
    >
      <Text size="sm">Письма не соберутся и не уйдут, пока не исправлено:</Text>
      <List size="sm" mt={4} spacing={4}>
        {view.missing.map((why) => (
          <List.Item key={why}>{why}</List.Item>
        ))}
      </List>
    </Alert>
  );
}

/** Цепочка языка одной строкой: полна ли и чья — словами сервера. */
function chainLine(state: ChainState): string {
  const whose = state.source === 'own' ? 'своя цепочка гипотезы' : 'общая цепочка';
  if (state.missing.length === 0) return `${whose} полна — лиды на этом языке получат письма.`;
  return `${whose}: нет ${state.missing.join(', ')} — лиды на этом языке ждут.`;
}

function Chains({ chains }: { chains: ChainState[] }) {
  return (
    <Stack gap={4} px="md">
      {chains.map((state) => (
        <Group key={state.language} gap="xs" wrap="nowrap" align="flex-start">
          <Badge
            variant="light"
            color={state.missing.length === 0 ? 'green' : 'yellow'}
            className="queueChainBadge"
          >
            {chainLanguageTitle(state.language)}
          </Badge>
          <Text size="sm" className="queueChain">
            {chainLine(state)}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

function BuildQueue({ view }: { view: SalesQueueView }) {
  const client = useQueryClient();
  const hypothesis = view.hypothesis_id;
  const [limit, setLimit] = useState(Math.min(DEFAULT_LIMIT, view.limit_max));
  const [jobId, setJobId] = useState<string | null>(() => remembered(buildJobKey(hypothesis)));
  const build = useMutation({
    mutationFn: () => buildSalesQueue({ hypothesis_id: hypothesis, limit }),
    onSuccess: (queued) => {
      setJobId(queued.job_id);
      remember(buildJobKey(hypothesis), queued.job_id);
      notify({ message: 'Сборка очереди ушла в очередь задач', color: 'green' });
    },
  });
  const empty = view.unwritten === 0 && view.queued === 0;
  return (
    <Stack gap={6} px="md">
      <Group align="flex-end" gap="md" wrap="wrap">
        {/* Пояснение — в «i» у подписи (`HintLabel`), как у «За раз» на «Письмах»: строка
            под полем раздувала его до своей ширины. Подпись — не `<label>`: в ней кнопка. */}
        <NumberInput
          label={<HintLabel label="Писем за раз" hint="Каждое стоит вызова модели." />}
          labelProps={{ labelElement: 'div' }}
          aria-label="Писем за раз"
          value={limit}
          min={1}
          max={view.limit_max}
          clampBehavior="strict"
          allowDecimal={false}
          // Поле — по числу (до трёх знаков), а не 180 px; колонка — по подписи: «Писем за раз»
          // с «i» в 6,5rem вставала в две строки (аудит экранов 09.10.2026, как у порогов).
          styles={{ wrapper: { width: '6.5rem' } }}
          onChange={(value) => setLimit(typeof value === 'number' ? value : DEFAULT_LIMIT)}
        />
        <Button
          className="press"
          disabled={!view.connected || empty}
          loading={build.isPending}
          onClick={() => build.mutate()}
        >
          Собрать очередь
        </Button>
      </Group>
      {empty ? (
        <Text size="sm" c="dimmed">
          Лидов без письма и писем в очереди у гипотезы нет — собирать нечего.
        </Text>
      ) : null}
      {build.error !== null ? (
        <Alert color="red" title="Очередь не собрана">
          {refusalOf(build.error)}
        </Alert>
      ) : null}
      {jobId !== null ? (
        <JobLine
          jobId={jobId}
          describe={queueLine}
          onFinished={() => void client.invalidateQueries({ queryKey: QUEUE_QUERY_KEY })}
        />
      ) : null}
    </Stack>
  );
}

function HypothesisQueue({ hypothesis }: { hypothesis: number }) {
  const client = useQueryClient();
  const { can } = useSession();
  const queue = useSalesQueue(hypothesis);
  const view = queue.data;
  if (view === undefined) {
    if (!queue.error) return <Loader aria-label="Загружаем очередь писем" m="md" />;
    return (
      <Alert color="red" title="Очередь писем не загрузилась">
        {refusalOf(queue.error)}
      </Alert>
    );
  }
  return (
    <Stack gap="md">
      <Connection view={view} />
      <Chains chains={view.chains} />
      <SimpleGrid cols={{ base: 1, xs: 3 }} spacing="sm" px="md">
        <Metric title="Лидов без письма" value={formatNumber(view.unwritten)} />
        <Metric title="Писем гипотезы в очереди" value={formatNumber(view.queued)} />
        <Metric title="В очереди продаж — всех гипотез" value={formatNumber(view.stage_queued)} />
      </SimpleGrid>
      <BuildQueue key={hypothesis} view={view} />
      {/* Без своей карточки: на телефоне её поля съедали ширину, и кнопка пачки
          резала своё число (снимок 390 px, 07.10.2026). */}
      {can('send') ? (
        <Stack gap={6} px="md">
          <Text size="sm" c="dimmed">
            Пачка берёт очередь продаж целиком — письма всех гипотез, не только выбранной.
          </Text>
          <SendQueue
            stage="sales"
            count={view.stage_queued}
            batchMax={view.batch_max}
            blocked={!view.connected}
            onFinished={() => void client.invalidateQueries({ queryKey: QUEUE_QUERY_KEY })}
          />
        </Stack>
      ) : null}
    </Stack>
  );
}

interface PaneProps {
  hypotheses: HypothesisCard[];
  /** Гипотеза раздела из адреса; не выбрана — первая: сборка идёт по одной гипотезе. */
  hypothesis: number | null;
  onHypothesis: (hypothesis: number) => void;
}

export function QueuePane({ hypotheses, hypothesis, onHypothesis }: PaneProps) {
  const chosen = hypothesis ?? hypotheses[0]?.id ?? null;
  if (chosen === null) {
    return (
      <Text size="sm" c="dimmed" px="md">
        Гипотез пока нет. Очередь собирается из лидов гипотезы — загрузите базу.
      </Text>
    );
  }
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="md" px="md">
        {/* Вступление — одной строкой, остальное — в «i» (аудит экранов 09.10.2026). */}
        <Group gap={4} wrap="nowrap" align="flex-start" style={{ flex: '1 1 20rem', minWidth: 0 }}>
          <Text size="sm">
            Сборка пишет первые письма лидам гипотезы, которые готовы к письмам, и ничего не
            отправляет.
          </Text>
          <InfoHint name="Как уходят письма продаж" width={340}>
            Уходят письма общей отправкой — пачкой или по одному; добивки идут сами, в той же
            переписке.
          </InfoHint>
        </Group>
        <Select
          label="Гипотеза"
          allowDeselect={false}
          data={hypotheses.map((row) => ({ value: String(row.id), label: row.name }))}
          value={String(chosen)}
          onChange={(value) => {
            if (value !== null) onHypothesis(Number(value));
          }}
          w={{ base: '100%', xs: 280 }}
        />
      </Group>
      <HypothesisQueue key={chosen} hypothesis={chosen} />
    </Stack>
  );
}
