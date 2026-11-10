/**
 * «Пишем» и «Не пишем» — одной парой во всех очередях экрана рекламодателей: в очереди проверки
 * (`CandidatesTable`) и у бизнесов ниши (`NicheQueue`).
 *
 * **Одного веса** (аудит экранов 09.10.2026, `docs/UI_RULES.md`): экран меряет ошибки скоринга, и
 * залитая «Пишем» в каждой строке подталкивала соглашаться с ним. **И одного вида на экране**
 * (разведка экранов 10.11.2026): у бизнесов ниши «Пишем» стояла залитой, а «Не пишем» — текстом,
 * в очереди над ними — светлой и с кромкой, и одно решение выглядело двумя разными.
 *
 * Пока решение строки уходит на сервер, «Пишем» крутится, а «Не пишем» закрыта: второе нажатие
 * отправило бы второе решение вслед первому.
 */

import { Button, Group } from '@mantine/core';

export function DecideButtons({
  busy,
  disabled = false,
  onDecide,
}: {
  /** Решение этой строки уходит на сервер. */
  busy: boolean;
  /** Решать нельзя: строка прежнего запроса, не того списка. */
  disabled?: boolean;
  onDecide: (write: boolean) => void;
}) {
  return (
    <Group gap="xs" justify="center" wrap="nowrap">
      <Button
        size="compact-sm"
        variant="light"
        className="press"
        loading={busy}
        disabled={disabled}
        onClick={() => onDecide(true)}
      >
        Пишем
      </Button>
      <Button
        size="compact-sm"
        variant="default"
        className="press"
        disabled={disabled || busy}
        onClick={() => onDecide(false)}
      >
        Не пишем
      </Button>
    </Group>
  );
}
