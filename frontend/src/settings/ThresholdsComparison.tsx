/**
 * Эти пороги против действующих — по метрикам доменов базы.
 *
 * **Одним правилом с обеих сторон** (проверка прода 10.10.2026). До этого карточка
 * звалась «Что станет с базой» и сравнивала несравнимое: «подходит сейчас» —
 * сохранённый вердикт, «будет подходить» — одни метрики. Вердикт же ставят ещё регион
 * и прошлые версии порогов, и любая правка «возвращала в базу» отсеянных регионом:
 * ключи 300 → 301 — «подходит 130, будет 135, вернётся 5». Теперь обе стороны
 * считает сервер одним правилом — тем, что у прогона (`runs/thresholds.py`): при
 * порогах, равных действующим, перемен ноль, строже — никого сверх не пропустят,
 * мягче — никого не отсекут.
 *
 * **Ни одно слово не обещает того, чего кнопка не сделает.** Сохранение вердикты в
 * базе не переписывает — новые пороги судят следующие замеры. Поэтому не «выпадет из
 * базы» и «вернётся в базу», а «отсекут» и «пропустят сверх действующих», и под
 * заголовком — что посчитано и чего сохранение не тронет.
 *
 * **Пересчёт не перерисовывает блок.** Пока идёт новый расчёт, прежние числа стоят
 * приглушёнными: значок загрузки на месте блока на каждое изменение порога
 * заставлял экран прыгать под рукой.
 */

import { Alert, Card, Loader, SimpleGrid, Stack, Text, Title } from '@mantine/core';

import { refusalOf } from '../api/client';
import type { ConsequencesView } from '../api/settings';
import { Metric } from '../components/Metric';
import { formatNumber, plural } from '../format';

interface Props {
  /** Черновик отличается от действующих порогов. */
  changed: boolean;
  /** Подпись первого поля с отказом; `null` — годятся все. */
  refused: string | null;
  data: ConsequencesView | undefined;
  fetching: boolean;
  error: unknown;
}

export function ThresholdsComparison(props: Props) {
  return (
    <Card className="glass" p="xl">
      <Title order={5} mb="sm">
        Эти пороги против действующих
      </Title>
      <ComparisonBody {...props} />
    </Card>
  );
}

function ComparisonBody({ changed, refused, data, fetching, error }: Props) {
  // Пороги, равные действующим, сервер сравнил бы с нулём перемен — тем же правилом
  // с обеих сторон; спрашивать его незачем.
  if (!changed) {
    return (
      <Text size="sm" c="dimmed">
        Пороги совпадают с действующими. Измените порог — здесь появится, скольких доменов базы
        новые отсекут и скольких пропустят сверх действующих.
      </Text>
    );
  }
  // Причину называет отказ под полем — здесь только какое поле: своя причина у
  // блока разошлась с ней («вне границ» над «только целое число», 10.10.2026).
  if (refused !== null) {
    return (
      <Text size="sm" c="dimmed">
        {`Поле «${refused}» не годится: что не так, сказано под ним. Поправьте его — и здесь появится сравнение с действующими.`}
      </Text>
    );
  }
  if (data !== undefined) return <Comparison data={data} stale={fetching} />;
  if (fetching) return <Loader size="sm" aria-label="Сравниваем с действующими" />;
  if (error) {
    return (
      <Alert color="red" title="Сравнение не посчиталось">
        {refusalOf(error)}
      </Alert>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      Сравнение считается по всей базе — появится, как только порог перестанет меняться.
    </Text>
  );
}

function Comparison({ data, stale }: { data: ConsequencesView; stale: boolean }) {
  return (
    <Stack
      gap="sm"
      className="staleRows"
      data-stale={stale || undefined}
      aria-busy={stale || undefined}
    >
      <Text size="sm" c="dimmed">
        {`По метрикам ${formatNumber(data.checked)} ${plural(data.checked, 'домена', 'доменов', 'доменов')} базы — тем же правилом, что у прогона. Вердикты в базе сохранение не переписывает: новые пороги судят следующие замеры.`}
      </Text>
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Metric title="Пропускают действующие" value={formatNumber(data.passing_now)} />
        <Metric title="Пропустят эти" value={formatNumber(data.passing_after)} />
        <Metric
          title="Отсекут сверх действующих"
          value={formatNumber(data.cut)}
          color={data.cut > 0 ? 'yellow' : undefined}
          hint={`из них с ценой: ${formatNumber(data.cut_with_price)}`}
        />
        <Metric title="Пропустят сверх действующих" value={formatNumber(data.admitted)} />
      </SimpleGrid>

      {data.cut_with_price > 0 && (
        <Alert color="yellow" title="Эти пороги отсекут домены с полученной ценой">
          За них заплачено не только юнитами, но и письмом. Сейчас их вердикт не изменится — новые
          пороги коснутся их при новом замере; знать это стоит до сохранения, а не после.
        </Alert>
      )}
      {data.undecided > 0 && (
        <Text size="sm" c="dimmed">
          {undecided(data.undecided)}
        </Text>
      )}
      {data.without_metrics > 0 && (
        <Text size="sm" c="dimmed">
          {withoutMetrics(data.without_metrics)}
        </Text>
      )}
    </Stack>
  );
}

/** Отсеянные действующими раньше остальных метрик: мягче порог пустил бы их дальше, а
 *  пройдут ли они там, скажет только замер — это не «пропустят». */
function undecided(count: number): string {
  const domains = plural(count, 'домен', 'домена', 'доменов');
  const them = count === 1 ? 'него' : 'них';
  return `Эти пороги пустили бы дальше ещё ${formatNumber(count)} ${domains}, но остальных метрик у ${them} нет — пройдут ли, решит новый замер.`;
}

/** Домены без метрик — словом и местоимением по числу: «ещё 1 домен: пороги его не
 *  судят», «ещё 21 домен: … их». До 10.10.2026 стояло «Ещё 1 доменов без метрик»
 *  (проверка прода 10.10.2026). */
function withoutMetrics(count: number): string {
  const domains = plural(count, 'домен', 'домена', 'доменов');
  const them = count === 1 ? 'его' : 'их';
  return `Без метрик — ещё ${formatNumber(count)} ${domains}: пороги ${them} не судят. Это повод добрать данные, а не отсев.`;
}
