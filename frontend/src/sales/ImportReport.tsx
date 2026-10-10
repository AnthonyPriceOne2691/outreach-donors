/**
 * Шаг «Отчёт» и итог загрузки: что получится — и что получилось.
 *
 * **Отчёт — по строкам, итог — по причинам.** До загрузки человек смотрит
 * на строки: номер строки файла, судьба, причина словами, ячейка как есть —
 * так строку находят в файле и чинят. После загрузки читают иначе: «загружено
 * 97, отклонено 3» и причины списком, частые первыми, с номерами строк (A1).
 *
 * **Отклонённая строка и строка с замечанием названы по-разному.** Первая
 * стоила лида, вторая загружена — поле, которое разрешено не знать (сайт,
 * страна, пояс, язык), непонятым строки не стоит. Смешать их в одной цифре
 * значило бы напугать «отклонено 500» там, где не распознана страна.
 *
 * **Числа — сервера.** «Станут лидами» до загрузки и «загружено» после —
 * один и тот же сухой прогон; экран ничего не пересчитывает.
 *
 * **Следующий шаг после загрузки — очистка, и она здесь же** (`CleanLeads`): без неё
 * лид не станет «готов к письмам». Её кнопка — главная на итоге (залитая), «К лидам» —
 * обычная: залитая кнопка на экране одна.
 */

import { Alert, Badge, Button, Group, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { countryTitle } from '../api/labels';
import type { ImportedLead, ImportProblem, IntakeView } from '../api/salesTypes';
import { Metric } from '../components/Metric';
import { formatNumber, plural } from '../format';
import { CleanLeads } from './CleanLeads';
import { groupProblems, linesOf } from './importMapping';
import type { ProblemGroup } from './importMapping';
import { NO_LEAD_FILTERS, writeLeadFilters } from './leadFilters';

/** Строк отчёта на экране сразу: отчёт о пяти тысячах строк с непонятой
 *  страной в каждой — тысячи строк разметки, а читают первые. */
const REPORT_ROWS = 300;

/** Лидов из предпросмотра на экране: по нескольким видно, что адрес и домен
 *  нормализованы, а страна узнана. Всего — в плитке. */
const SHOWN_LEADS = 5;

/** Как назвать судьбу строки: отклонена — или загружена, но с замечанием.
 *  Оба значка — `light`: у контурного красный текст на светлом стекле намерился
 *  4,23 : 1 при норме 4,5 (04.10.2026), а `light` подтягивает текст к чернилам
 *  общим правилом `glass.css`. */
function Fate({ loaded }: { loaded: boolean }) {
  return loaded ? (
    <Badge variant="light" color="yellow">
      загружен с замечанием
    </Badge>
  ) : (
    <Badge variant="light" color="red">
      строка отклонена
    </Badge>
  );
}

/** Отклонённые строки первыми — они стоили лидов, — внутри по порядку файла. */
function ordered(problems: ImportProblem[]): ImportProblem[] {
  return [...problems].sort((a, b) => Number(a.loaded) - Number(b.loaded) || a.line - b.line);
}

export function ProblemsTable({ problems }: { problems: ImportProblem[] }) {
  const [all, setAll] = useState(false);
  if (problems.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Все строки прочитаны без замечаний.
      </Text>
    );
  }
  const rows = ordered(problems);
  const shown = all ? rows : rows.slice(0, REPORT_ROWS);
  return (
    <Stack gap="xs">
      <Table.ScrollContainer minWidth={760} type="native" className="scrollSlim">
        <Table
          aria-label="Отчёт по строкам"
          className="dataTable fixedTable reportTable"
          layout="fixed"
          verticalSpacing="xs"
          horizontalSpacing="sm"
        >
          <colgroup>
            <col style={{ width: '6rem' }} />
            <col style={{ width: '13rem' }} />
            <col />
            <col style={{ width: '14rem' }} />
          </colgroup>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Строка</Table.Th>
              <Table.Th>Что с ней</Table.Th>
              <Table.Th>Почему</Table.Th>
              <Table.Th>Ячейка</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((problem) => (
              <Table.Tr key={`${problem.line}:${problem.reason}`}>
                <Table.Td>
                  <Text size="sm">{formatNumber(problem.line)}</Text>
                </Table.Td>
                <Table.Td>
                  <Fate loaded={problem.loaded} />
                </Table.Td>
                <Table.Td className="wrapCell">
                  <Text size="sm">{problem.reason}</Text>
                </Table.Td>
                <Table.Td className="wrapCell">
                  <Text size="xs" c="dimmed" className="reportCell">
                    {problem.cell === '' ? '—' : problem.cell}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {shown.length < rows.length && (
        <Group gap="sm">
          <Text size="sm" c="dimmed">
            Показаны первые {formatNumber(shown.length)} из {formatNumber(rows.length)} строк
            отчёта.
          </Text>
          <Button variant="subtle" size="compact-sm" className="press" onClick={() => setAll(true)}>
            Показать все
          </Button>
        </Group>
      )}
    </Stack>
  );
}

function LeadsPreview({ leads, total }: { leads: ImportedLead[]; total: number }) {
  const shown = leads.slice(0, SHOWN_LEADS);
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        Первые лиды — адрес и домен компании уже приведены к одному виду
      </Text>
      <Table.ScrollContainer minWidth={760} type="native" className="scrollSlim">
        <Table className="dataTable fixedTable" layout="fixed" verticalSpacing="xs">
          <colgroup>
            <col style={{ width: '17rem' }} />
            <col style={{ width: '13rem' }} />
            <col style={{ width: '12rem' }} />
            <col style={{ width: '12rem' }} />
            <col style={{ width: '9rem' }} />
          </colgroup>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Адрес</Table.Th>
              <Table.Th>Имя</Table.Th>
              <Table.Th>Компания</Table.Th>
              <Table.Th>Домен</Table.Th>
              <Table.Th>Страна</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((lead) => (
              <Table.Tr key={lead.line}>
                <Table.Td className="cellName">
                  <Text size="sm">{lead.email}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{lead.name ?? '—'}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{lead.company ?? '—'}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{lead.domain}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{countryTitle(lead.country)}</Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {total > shown.length && (
        <Text size="xs" c="dimmed">
          Показаны {formatNumber(shown.length)} из {formatNumber(total)}.
        </Text>
      )}
    </Stack>
  );
}

interface ReportProps {
  found: IntakeView;
  hypothesisName: string | null;
  busy: boolean;
  refusal: string | null;
  onBack: () => void;
  onLoad: () => void;
}

/** Что получится: сводка, первые лиды, отчёт по строкам — и кнопка записи. */
export function ImportReport({
  found,
  hypothesisName,
  busy,
  refusal,
  onBack,
  onLoad,
}: ReportProps) {
  const notes = found.problems.filter((problem) => problem.loaded).length;
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={5}>Что получится</Title>
        <Text size="sm" c="dimmed" maw={720}>
          Сухой прогон: в базу пока ничего не записано. Так же посчитает и загрузка.
          {hypothesisName !== null && ` Лиды лягут в гипотезу «${hypothesisName}».`}
        </Text>
      </Stack>
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Metric title="Строк в файле" value={formatNumber(found.rows)} />
        <Metric
          title="Станут лидами"
          value={formatNumber(found.accepted)}
          color={found.accepted > 0 ? 'green' : undefined}
        />
        <Metric
          title="Отклонено"
          value={formatNumber(found.rejected)}
          color={found.rejected > 0 ? 'red' : undefined}
        />
        <Metric title="С замечанием" value={formatNumber(notes)} />
      </SimpleGrid>
      {found.leads.length > 0 && <LeadsPreview leads={found.leads} total={found.accepted} />}
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          Отчёт по строкам
        </Text>
        <ProblemsTable problems={found.problems} />
      </Stack>
      {refusal !== null && (
        <Alert color="red" title="Не загрузилось">
          {refusal}
        </Alert>
      )}
      <Group justify="space-between">
        <Button variant="subtle" className="press" onClick={onBack}>
          К колонкам
        </Button>
        <Button className="press" loading={busy} disabled={found.accepted === 0} onClick={onLoad}>
          Загрузить {formatNumber(found.accepted)} {plural(found.accepted, 'лид', 'лида', 'лидов')}
        </Button>
      </Group>
    </Stack>
  );
}

function Reasons({ title, groups }: { title: string; groups: ProblemGroup[] }) {
  if (groups.length === 0) return null;
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        {title}
      </Text>
      {groups.map((group) => (
        <Text key={group.reason} size="sm" component="p" m={0}>
          <Text component="span" size="sm" fw={500}>
            {group.reason} — {formatNumber(group.lines.length)}
          </Text>
          <Text component="span" size="sm" c="dimmed">
            {' · '}
            {linesOf(group.lines)}
          </Text>
        </Text>
      ))}
    </Stack>
  );
}

interface OutcomeProps {
  outcome: IntakeView;
  hypothesisId: number;
  hypothesisName: string | null;
  /** Лидов гипотезы, ждущих очистки, — из списка гипотез: с загруженными и прежние. */
  waiting: number;
  onAgain: () => void;
}

/** Итог загрузки: сколько записано и почему не всё — словами, по причинам; очистка — тут же. */
export function ImportOutcome({
  outcome,
  hypothesisId,
  hypothesisName,
  waiting,
  onAgain,
}: OutcomeProps) {
  const loaded = outcome.loaded ?? outcome.accepted;
  const leadsAt = `/sales?${writeLeadFilters({ ...NO_LEAD_FILTERS, hypothesis: hypothesisId }).toString()}`;
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Title order={4}>
          Загружено {formatNumber(loaded)}, отклонено {formatNumber(outcome.rejected)}.
        </Title>
        <Text size="sm" c="dimmed" maw={720}>
          {hypothesisName !== null && `В гипотезу «${hypothesisName}». `}
          Лиды записаны новыми: до писем их проверит очистка — дубли, стоп-лист, почта домена,
          проверка адреса.
        </Text>
      </Stack>
      <CleanLeads hypothesis={hypothesisId} waiting={waiting} primary />
      <Reasons title="Почему отклонены" groups={groupProblems(outcome.problems, false)} />
      <Reasons title="Загружены с замечанием" groups={groupProblems(outcome.problems, true)} />
      <Group>
        <Button component={Link} to={leadsAt} variant="default" className="press">
          К лидам
        </Button>
        <Button variant="subtle" className="press" onClick={onAgain}>
          Загрузить ещё
        </Button>
      </Group>
      {outcome.problems.length > 0 && (
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            По строкам
          </Text>
          <ProblemsTable problems={outcome.problems} />
        </Stack>
      )}
    </Stack>
  );
}
