/**
 * «N лидов ждут очистки» и кнопка «Очистить» — на вкладке лидов и в итоге загрузки базы.
 *
 * До этой кнопки очистка шла только командой консоли, и без неё лид не становился «готов
 * к письмам» — очередь писем стояла пустой. Теперь очистка — задача очереди продаж
 * (`POST /sales/clean`), тот же путь, что у консоли: дубли, стоп-листы, почта домена,
 * проверка адреса.
 *
 * **Перед запуском экран спрашивает сервер** (`GET /sales/clean`, ход — `useCleaning`):
 * сколько лидов ждут и платная ли проверка адресов — на момент нажатия, а не по списку
 * гипотез, который мог устареть: подтверждают деньги. Живая проверка (Hunter общим ключом) —
 * окно «проверка адресов платная: до N запросов Hunter»: каждый лид стоит не больше одной
 * проверки. Выдуманная (`fixture`) — без окна, сразу в очередь.
 *
 * **Отказ — словами там, где нажали**: в окне, если оно открыто (окно остаётся с прежней
 * кнопкой), иначе — под кнопкой. Ход задачи — строкой `JobLine`; кончилась — лиды и
 * счётчики раздела перечитываются, и таблица показывает, кто готов, а кто отсеян.
 *
 * **Право — `run`**: в очистке платная проверка адресов, как в поиске адресов ядра. Без
 * права — объяснение на месте, а не пропавшая кнопка.
 */

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import type { MantineSpacing } from '@mantine/core';

import { refusalOf } from '../api/client';
import { PERMISSION_TITLES } from '../api/labels';
import type { SalesCleanView } from '../api/salesTypes';
import { useSession } from '../auth/AuthProvider';
import { formatNumber, plural } from '../format';
import { JobLine } from '../jobs/JobLine';
import { cleanLine, useCleaning } from './cleanData';

/** «12 лидов ждут очистки» — число одно, глагол — по числу. */
export function waitingLine(count: number): string {
  const leads = plural(count, 'лид', 'лида', 'лидов');
  return `${formatNumber(count)} ${leads} ${plural(count, 'ждёт', 'ждут', 'ждут')} очистки`;
}

interface ConfirmProps {
  view: SalesCleanView;
  busy: boolean;
  error: Error | null;
  onCancel: () => void;
  onStart: () => void;
}

/** Окно расхода: сколько платных запросов самое большее и что их станет меньше. Фокус — на
 *  «Отмене»: подтверждают деньги, и Enter по привычке не должен их тратить. */
function ConfirmPaid({ view, busy, error, onCancel, onStart }: ConfirmProps) {
  const requests = plural(view.waiting, 'запроса', 'запросов', 'запросов');
  return (
    <Modal opened onClose={onCancel} title="Запустить очистку?">
      <Stack gap="sm">
        <Text size="sm">
          Проверка адресов платная: до {formatNumber(view.waiting)} {requests} Hunter.
        </Text>
        <Text size="sm" c="dimmed">
          Каждый лид — не больше одной проверки. Дубли, стоп-листы и домены без почты отсеиваются
          раньше и бесплатно, поэтому запросов обычно меньше. Ключ Hunter общий с поиском адресов.
        </Text>
        {error !== null ? (
          <Alert color="red" title="Очистка не запущена">
            {refusalOf(error)}
          </Alert>
        ) : null}
        <Group justify="flex-end">
          <Button variant="default" data-autofocus disabled={busy} onClick={onCancel}>
            Отмена
          </Button>
          <Button className="press" loading={busy} onClick={onStart}>
            Очистить
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

interface RowProps {
  waiting: number;
  primary: boolean;
  busy: boolean;
  onPress: () => void;
}

/** Сколько ждёт — и кнопка, или кто её нажимает: без права — объяснение на месте. */
function WaitingRow({ waiting, primary, busy, onPress }: RowProps) {
  const { can } = useSession();
  return (
    <Group gap="md" wrap="wrap">
      <Text size="sm">{waitingLine(waiting)}</Text>
      {can('run') ? (
        <Button
          variant={primary ? 'filled' : 'default'}
          className="press"
          loading={busy}
          onClick={onPress}
        >
          Очистить
        </Button>
      ) : (
        <Text size="sm" c="dimmed">
          Очистку запускает сотрудник с правом «{PERMISSION_TITLES.run}».
        </Text>
      )}
    </Group>
  );
}

interface Props {
  hypothesis: number;
  /** Лидов «новый» у гипотезы — из списка гипотез: то же число, что у плитки «Новые». */
  waiting: number;
  /** Главная ли это кнопка экрана. В итоге загрузки — да; на вкладке лидов залита
   *  «Загрузить базу», и вторая залитая спорила бы с ней. */
  primary?: boolean;
  /** Поле по бокам — у самой строки: пустой обёртки, когда строки нет, не остаётся. */
  px?: MantineSpacing;
}

export function CleanLeads({ hypothesis, waiting, primary = false, px = 0 }: Props) {
  const cleaning = useCleaning(hypothesis);
  if (waiting === 0 && cleaning.jobId === null) return null;
  return (
    <Stack gap={6} px={px}>
      {waiting > 0 ? (
        <WaitingRow
          waiting={waiting}
          primary={primary}
          busy={cleaning.busy}
          onPress={cleaning.press}
        />
      ) : null}
      {cleaning.refusal !== null ? (
        <Alert color="red" title="Очистка не запущена">
          {refusalOf(cleaning.refusal)}
        </Alert>
      ) : null}
      {cleaning.jobId !== null ? (
        <JobLine jobId={cleaning.jobId} describe={cleanLine} onFinished={cleaning.finished} />
      ) : null}
      {cleaning.paid !== null ? (
        <ConfirmPaid
          view={cleaning.paid}
          busy={cleaning.starting}
          error={cleaning.startError}
          onCancel={cleaning.cancel}
          onStart={cleaning.confirm}
        />
      ) : null}
    </Stack>
  );
}
