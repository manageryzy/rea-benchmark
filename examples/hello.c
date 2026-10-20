#include <stdio.h>
#include <stdlib.h>

__attribute__((noinline)) int calculate_total(int quantity, int price) {
    int subtotal = quantity * price;
    return subtotal >= 100 ? subtotal - 10 : subtotal;
}

int main(int argc, char **argv) {
    int quantity = argc > 1 ? atoi(argv[1]) : 3;
    printf("Total: %d\n", calculate_total(quantity, 25));
    return 0;
}
