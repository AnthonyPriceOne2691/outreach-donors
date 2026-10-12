/**
 * Качество агента продаж: как люди обходятся с его черновиками — шаг «диагноз» докрутки.
 *
 * **Экран ничего не решает и не считает сам.** Числа, итог ворот автоответа и его слова —
 * сервера (`GET /sales/agent-quality`); экран делит доли и только там, где основа не меньше N,
 * — ниже «данных мало» (`qualityData.ts`). Флага автоответа здесь нет: его включает человек.
 *
 * **Версия агента — одна на экран** (`?cohort=`): числа разных версий промптов и базы знаний
 * не смешиваются. Без номера — текущая версия, а если она ещё не писала — последняя писавшая.
 * Таблица версий ниже — для сравнения, каждая строка по своим черновикам.
 *
 * Сверху вниз: плитки по показанной версии, ситуации с воротами автоответа, причины
 * отклонения, версии агента, последние правки и отклонения «черновик → ушло».
 */

import { Alert, Group, Loader, Select, SimpleGrid, Stack, Text } from '@mantine/core';

import { refusalOf } from '../api/client';
import type { AgentQualityView, CohortRow } from '../api/salesQuality';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { dropdownBelow } from '../theme';
import { QualityChanges } from './QualityChanges';
import {
  decisionsOf,
  heldOf,
  shareText,
  silencesOf,
  useAgentQuality,
  versionDetail,
  versionName,
} from './qualityData';
import { QualityReasons, QualitySituations, QualityVersions } from './QualityTables';

function Intro({ view }: { view: AgentQualityView }) {
  const { min_n: least, settle_days: days } = view.limits;
  return (
    <Group gap={4} wrap="nowrap" align="flex-start" px="md">
      <Text size="sm" className="qualityIntro">
        Как оператор обходится с черновиками агента: отправил как есть, с правкой или отклонил — и
        где агент промолчал зря.
      </Text>
      <InfoHint name="Как считается качество агента" width={360}>
        Доли — от решений оператора по готовым черновикам: как есть, с правкой, отклонено. Ждущие,
        отданные человеку и молчание в основу не входят. Ложное молчание — агент решил «ответ не
        нужен», а человек ответил; молчание человека — исход через {days} дн. Меньше {least} решений
        — «данных мало», без процента. Ушёл бы отданный человеку черновик как есть, журнал не знает:
        у такого «как есть» закрыто.
      </InfoHint>
    </Group>
  );
}

function VersionPick({
  view,
  onCohort,
}: {
  view: AgentQualityView;
  onCohort: (cohort: string) => void;
}) {
  const label = (row: CohortRow) => `${versionName(row.cohort)}${row.current ? ' — текущая' : ''}`;
  const fresh = view.shown.id !== view.current.id;
  return (
    <Stack gap={4} px="md">
      <Select
        label="Версия агента"
        allowDeselect={false}
        data={view.cohorts.map((row) => ({ value: row.cohort.id, label: label(row) }))}
        value={view.shown.id}
        onChange={(value) => {
          if (value !== null) onCohort(value);
        }}
        comboboxProps={dropdownBelow}
        maw="36rem"
        className="qualityPick"
      />
      <Text size="xs" c="dimmed" className="qualityVersion">
        {versionDetail(view.shown)}
      </Text>
      {fresh && !view.cohorts.some((row) => row.current) ? (
        <Text size="sm" className="qualityFresh">
          Текущая версия агента черновиков ещё не писала — числа ниже по прежней.
        </Text>
      ) : null}
    </Stack>
  );
}

function Tiles({ view, stale }: { view: AgentQualityView; stale: boolean }) {
  const { counts, limits } = view;
  const decisions = decisionsOf(counts);
  const silences = silencesOf(counts);
  return (
    <SimpleGrid
      cols={{ base: 2, sm: 3, lg: 6 }}
      spacing="sm"
      px="md"
      className="staleRows qualityTiles"
      data-stale={stale || undefined}
      aria-busy={stale || undefined}
    >
      <Metric
        title="Решений оператора"
        value={formatNumber(decisions)}
        hint={`ждут решения: ${formatNumber(counts.waiting)}`}
      />
      <Metric
        title="Как есть"
        value={shareText(counts.as_is, decisions, limits, limits.as_is)}
        hint={`${formatNumber(counts.as_is)} из ${formatNumber(decisions)}`}
      />
      <Metric
        title="С правкой"
        value={shareText(counts.edited, decisions, limits)}
        hint={`${formatNumber(counts.edited)} из ${formatNumber(decisions)}`}
      />
      <Metric
        title="Отклонено"
        value={shareText(counts.rejected, decisions, limits)}
        hint={`${formatNumber(counts.rejected)} из ${formatNumber(decisions)}`}
      />
      <Metric
        title="Ложное молчание"
        value={formatNumber(counts.false_silence)}
        hint={`из ${formatNumber(silences)} молчаний; верных — ${shareText(
          counts.true_silence,
          silences,
          limits,
          limits.silence,
        )}`}
        info="Агент решил «ответ не нужен», а человек ответил. Молчание агента с исходом — когда человек ответил или промолчал дольше срока."
      />
      <Metric
        title="Отдано человеку"
        value={formatNumber(heldOf(counts))}
        hint={`отправлено ${formatNumber(counts.held_sent)} · отклонено ${formatNumber(
          counts.held_rejected,
        )}`}
        info="Агент не взялся, засомневался, судья не пропустил — или понял «хочет говорить», и лид ушёл телемаркетологу. «Как есть» у такого закрыто, и в доли он не входит."
      />
    </SimpleGrid>
  );
}

interface PaneProps {
  /** Версия агента из адреса раздела; пусто — текущая. */
  cohort: string;
  onCohort: (cohort: string) => void;
}

export function AgentQualityPane({ cohort, onCohort }: PaneProps) {
  const quality = useAgentQuality(cohort);
  const view = quality.data;
  if (view === undefined) {
    return quality.error ? (
      <Alert color="red" title="Качество агента не загрузилось" mx="md">
        {refusalOf(quality.error)}
      </Alert>
    ) : (
      <Loader aria-label="Загружаем качество агента" m="md" />
    );
  }
  const stale = quality.isPlaceholderData;
  if (view.cohorts.length === 0) {
    return (
      <Stack gap="md">
        <Intro view={view} />
        <Text size="sm" px="md" className="qualityEmpty">
          Черновиков агента продаж ещё нет: агент пишет их на вопросы и интерес лидов, когда он
          включён. Числа появятся с первыми решениями оператора.
        </Text>
      </Stack>
    );
  }
  return (
    <Stack gap="md">
      <Intro view={view} />
      <VersionPick view={view} onCohort={onCohort} />
      <Tiles view={view} stale={stale} />
      <QualitySituations view={view} stale={stale} />
      <QualityReasons view={view} stale={stale} />
      <QualityVersions view={view} stale={stale} />
      <QualityChanges changes={view.changes} />
    </Stack>
  );
}
