PHDRS {
    text PT_LOAD FLAGS(5);
    rodata PT_LOAD FLAGS(4);
    data PT_LOAD FLAGS(6);
}
SECTIONS {
    . = 0x01000000;
    .text : { *(.text .text.*) } :text
    . = ALIGN(4096);
    .rodata : { BYTE(0) *(.rodata .rodata.*) } :rodata
    . = ALIGN(4096);
    .data : { BYTE(0) *(.data .data.*) *(.got .got.*) } :data
    .bss : { *(.bss .bss.*) *(COMMON) } :data
    /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
